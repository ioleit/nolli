"""A GitHub pull request as a change for ``nolli view``: the layer on top of plain git
(``nolli/change.py``) that knows one host.

``nolli view <url>`` (https://github.com/<owner>/<repo>/pull/<n>) or ``nolli view '#<n>'``
(on the repository's GitHub remote) asks GitHub's REST API for the PR's title,
description, base and head, fetches the head (``pull/<n>/head``) and the base branch into
the clone, and gives the change GitHub's "Files changed" tab shows: from where the head
left the base branch to the head. The fetch stores no branch or tag, only objects (and
FETCH_HEAD). A token, if one is needed (a private repository, or more than 60 requests an
hour), comes from $GH_TOKEN or $GITHUB_TOKEN, else from the gh CLI if it is logged in.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .change import Change, ChangeError, git, merge_base

URL = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/pulls?/(\d+)(?:[/?#].*)?$")
NUMBER = re.compile(r"^(?:#|pr:|pr/)(\d+)$", re.IGNORECASE)
REMOTE = re.compile(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")


def is_pull_request(spec: str | None) -> bool:
    return bool(spec and (URL.match(spec) or NUMBER.match(spec)))


def remotes(repo: Path) -> dict[str, tuple[str, str]]:
    """The repository's GitHub remotes: name -> (owner, repo)."""
    out = {}
    for line in git(repo, "remote", "-v").splitlines():
        name, url, *_ = line.split()
        m = REMOTE.match(url)
        if m:
            out[name] = (m.group(1), m.group(2))
    return out


def resolve(repo: Path, spec: str) -> tuple[Change, dict]:
    """The PR's change, and what the dataset records about it: ``{"pr", "remote"}``."""
    m = URL.match(spec)
    gh = remotes(repo)
    if m:
        owner, name, number = m.group(1), m.group(2), int(m.group(3))
    else:
        number = int(NUMBER.match(spec).group(1))
        pick = "upstream" if "upstream" in gh else "origin" if "origin" in gh else next(iter(gh), None)
        if pick is None:
            raise ChangeError(f"{repo} has no GitHub remote; give the PR's URL, or its commits: nolli view <base>..<head>")
        owner, name = gh[pick]
    pr = api(f"repos/{owner}/{name}/pulls/{number}")
    # fetch from the clone's own remote for that repository if it has one (its credentials), else by URL
    remote = next((r for r, (o, n) in gh.items() if (o.lower(), n.lower()) == (owner.lower(), name.lower())),
                  f"https://github.com/{owner}/{name}")
    try:
        git(repo, "fetch", "-q", "--no-tags", "--no-write-fetch-head", remote,
            f"pull/{number}/head", pr["base"]["ref"])
    except ChangeError as exc:
        raise ChangeError(f"could not fetch PR #{number} from {remote}: {exc}") from None
    head, base_tip = pr["head"]["sha"], pr["base"]["sha"]
    for sha in (head, base_tip):
        if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False).returncode:
            raise ChangeError(f"commit {sha[:7]} of PR #{number} is not in {repo} after fetching it")
    ch = Change(head=head, base=merge_base(repo, base_tip, head), title=pr.get("title") or f"PR #{number}",
                description=(pr.get("body") or "").replace("\r\n", "\n").strip())
    return ch, {"pr": number, "remote": f"https://github.com/{owner}/{name}"}


def token() -> str | None:
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t or not shutil.which("gh"):
        return t
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    return r.stdout.strip() or None if r.returncode == 0 else None


def api(path: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com/{path}", headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nolli"})
    t = token()
    if t:
        req.add_header("Authorization", f"Bearer {t}")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        hint = "" if t else " (no token: set GH_TOKEN, or log in with gh, for a private repository)"
        raise ChangeError(f"GitHub: {path}: {exc.code} {exc.reason}{hint}") from None
    except urllib.error.URLError as exc:
        raise ChangeError(f"GitHub: {path}: {exc.reason}") from None
