"""Utils for GitHub repos and PRs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from nolli.logic.sources import CloneCache, git
from nolli.logic.sources.git import ChangeError, ChangeSpec

URL = re.compile(r"^https?://github\.com/([^/]+)/([^/?#]+?)(?:\.git)?(/[^?#]*)?(?:[?#].*)?$")
NUMBER = re.compile(r"^(?:#|pr:|pr/)(\d+)$", re.IGNORECASE)
REMOTE = re.compile(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")


@dataclass
class Target:
    """A target on GitHub (nolli/cli.py lists the forms): a repository at its default branch
    (``ref`` None) or at ``ref``, a commit's own change, a comparison, or a pull request."""
    owner: str
    name: str
    kind: str  # "repo", "commit", "compare", "pull"
    ref: str | None = None  # repo: the branch, tag or commit; commit: the sha
    base: str | None = None  # compare
    head: str | None = None  # compare
    symmetric: bool = True  # compare: <base>...<head> (else <base>..<head>)
    number: int | None = None  # pull


def parse_target(text: str | None, local: Path | None = None) -> Target | None:
    """The GitHub target ``text`` names, None if it names none: a URL of github.com, or
    ``#<n>`` (also ``pr:<n>``, ``pr/<n>``) for a pull request of ``local``'s GitHub remote
    (``upstream``, else ``origin``, else any). Raises ChangeError for a github.com URL nolli
    cannot build, or ``#<n>`` without a GitHub remote."""
    if not text:
        return None
    n = NUMBER.match(text)
    if n:
        gh = _remotes(local) if local is not None else {}
        pick = "upstream" if "upstream" in gh else "origin" if "origin" in gh else next(iter(gh), None)
        if pick is None:
            raise ChangeError(f"{text}: {local or 'this directory'} has no GitHub remote; give the pull request's URL")
        return Target(*gh[pick], "pull", number=int(n.group(1)))
    m = URL.match(text)
    if not m:
        return None
    owner, name, rest = m.group(1), m.group(2), (m.group(3) or "").strip("/")
    part, _, tail = rest.partition("/")
    if not rest:
        return Target(owner, name, "repo")
    if part == "tree" and tail:
        return Target(owner, name, "repo", ref=tail)
    if part == "commit" and tail:
        return Target(owner, name, "commit", ref=tail.split("/")[0])
    if part in ("pull", "pulls") and tail.split("/")[0].isdigit():
        return Target(owner, name, "pull", number=int(tail.split("/")[0]))
    if part == "compare" and ".." in tail:
        symmetric = "..." in tail
        base, _, head = tail.partition("..." if symmetric else "..")
        if base and head:
            return Target(owner, name, "compare", base=base, head=head, symmetric=symmetric)
    raise ChangeError(f"{text}: nolli builds a repository, /tree/<ref>, /commit/<sha>, /compare/<a>...<b> or /pull/<n> on GitHub")


def _remotes(repo: Path) -> dict[str, tuple[str, str]]:
    """The repository's GitHub remotes: name -> (owner, repo)."""
    out = {}
    for line in git.run_git(repo, "remote", "-v").splitlines():
        name, url, *_ = line.split()
        m = REMOTE.match(url)
        if m:
            out[name] = (m.group(1), m.group(2))
    return out


AMBIENT = object()  # the token of whoever runs nolli: $GH_TOKEN, $GITHUB_TOKEN or gh (find_token())


@dataclass
class Answer:
    """What GitHub says a target is: the commits to fetch, and what the change says about
    itself. ``base`` is the change's base, or with ``merge`` the tip it left (the change starts
    where head and base diverged, which needs the commits: ``finish_target``)."""
    head: str
    base: str | None = None
    merge: bool = False
    title: str = ""
    description: str = ""
    pr: int | None = None


def ask_target(t: Target, get: Callable[[str], dict] | None = None) -> Answer:
    """What ``t`` names, as GitHub's REST API says. ``get``: how a path is asked (default:
    api(), with the token of whoever runs nolli); a server passes its own (its token, its cache)."""
    get = get or api
    path = f"repos/{t.owner}/{t.name}"
    if t.kind == "pull":
        pr = get(f"{path}/pulls/{t.number}")
        return Answer(pr["head"]["sha"], pr["base"]["sha"], merge=True, title=pr.get("title") or f"PR #{t.number}",
                      description=(pr.get("body") or "").replace("\r\n", "\n").strip(), pr=t.number)
    if t.kind == "commit":
        c = get(f"{path}/commits/{quote(t.ref or '', safe='')}")
        parents = [p["sha"] for p in c.get("parents", [])]
        if not parents:
            raise ChangeError(f"{t.owner}/{t.name}@{c['sha'][:7]} has no parent; build it as a snapshot: "
                              f"https://github.com/{t.owner}/{t.name}/tree/{c['sha']}")
        subject, _, body = (c.get("commit", {}).get("message") or "").partition("\n")
        return Answer(c["sha"], parents[0], title=subject, description=body.strip())
    if t.kind == "compare":
        head, base = (get(f"{path}/commits/{quote(r or '', safe='')}")["sha"] for r in (t.head, t.base))
        return Answer(head, base, merge=t.symmetric)
    ref = t.ref or get(path)["default_branch"]
    return Answer(get(f"{path}/commits/{quote(ref, safe='')}")["sha"], title=f"{t.owner}/{t.name} at {ref}")


def fetch_into(repo: Path, owner: str, name: str, shas: list[str]) -> None:
    """Fetch ``shas`` of github.com/<owner>/<name> into the local repository ``repo``, objects
    only (no branch or tag): from its own remote for that repository if it has one (its
    credentials), else by URL."""
    missing = [s for s in shas if not git.has_commit(repo, s)]
    if not missing:
        return
    remote = next((r for r, (o, n) in _remotes(repo).items() if (o.lower(), n.lower()) == (owner.lower(), name.lower())),
                  f"https://github.com/{owner}/{name}")
    try:
        git.run_git(repo, "fetch", "-q", "--no-tags", "--no-write-fetch-head", remote, *missing)
    except git.GitError as exc:
        raise ChangeError(f"could not fetch {', '.join(s[:7] for s in missing)} from {remote}: {exc}") from None


def finish_target(t: Target, a: Answer, clone: Path) -> ChangeSpec:
    """The change (or snapshot) ``a`` names, in ``clone``, which has its commits."""
    if a.base is None:
        return ChangeSpec(head=a.head, title=a.title) if a.title else git.describe(clone, ChangeSpec(head=a.head), a.head)
    base = git.merge_base(clone, a.base, a.head) if a.merge else a.base
    if base == a.head:
        raise ChangeError(f"{t.owner}/{t.name}: no change ({a.head[:7]} is on {a.base[:7]})")
    ch = ChangeSpec(head=a.head, base=base, title=a.title, description=a.description)
    return ch if a.title else git.describe(clone, ch, t.head or a.head)


def make_clone_cache(root: Path) -> CloneCache:
    """Bare clones under ``root``, fetched with the token of whoever runs nolli."""
    return CloneCache(root, lambda owner, name: find_token())


class NotFound(ChangeError):
    """GitHub answered 404: no such thing, or one the token (or nobody) may not see."""


def find_token() -> str | None:
    """A GitHub token, for when one is needed (a private repository, or more than 60 requests
    an hour): $GH_TOKEN or $GITHUB_TOKEN, else the gh CLI's if it is logged in; None if
    there is none."""
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t or not shutil.which("gh"):
        return t
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    return r.stdout.strip() or None if r.returncode == 0 else None


def api(path: str, tok: str | None | object = AMBIENT) -> dict:
    """GET a GitHub REST path. ``tok``: AMBIENT (the default) uses find_token(); a server passes the
    viewer's own token, or None to ask as nobody (public repositories only)."""
    req = urllib.request.Request(f"https://api.github.com/{path}", headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nolli"})
    t = find_token() if tok is AMBIENT else tok
    if t:
        req.add_header("Authorization", f"Bearer {t}")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and tok is not AMBIENT:
            raise NotFound(f"GitHub: {path}: 404") from None
        hint = "" if t else " (no token: set GH_TOKEN, or log in with gh, for a private repository)"
        raise ChangeError(f"GitHub: {path}: {exc.code} {exc.reason}{hint}") from None
    except urllib.error.URLError as exc:
        raise ChangeError(f"GitHub: {path}: {exc.reason}") from None
