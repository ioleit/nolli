"""What ``nolli view`` shows: a change between two commits of a git repository, or one
commit as a whole, resolved from what the user typed.

Plain git only; nothing here knows about GitHub or any other host.

- nothing: the current branch against the default branch (from where they diverged to
  HEAD); on the default branch itself, or with nothing new on the branch, HEAD as a whole;
- ``A..B``: exactly from A to B;
- ``A...B``: from where A and B diverged to B (what B adds, as a pull request shows it);
- ``--whole`` (a separate option): HEAD, or the commit given, as a whole;
- one revision R: what R adds to the default branch, from where they diverged to R; if R
  is already on the default branch (a commit, or a merged pull request's merge commit),
  R's own change against its first parent.

The default branch is ``origin/HEAD`` if the clone has it, else ``main`` or ``master``
(remote-tracking first). Only commits count: changes in the working tree are not seen.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class ChangeError(Exception):
    """A change that cannot be resolved; the message says what to type instead."""


@dataclass
class Change:
    head: str                  # full sha
    base: str | None = None    # full sha; None: the head commit as a whole, no change
    title: str = ""            # what the viewer's header calls it
    description: str = ""      # what review.md quotes as the change's description (markdown)

    @property
    def is_diff(self) -> bool:
        return self.base is not None


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise ChangeError(exc.stderr.strip() or f"git {' '.join(args)} failed") from None


def toplevel(path: Path) -> Path:
    """The root of the git working tree that ``path`` is in."""
    try:
        return Path(git(path, "rev-parse", "--show-toplevel").strip())
    except ChangeError:
        raise ChangeError(f"{path} is not in a git repository") from None


def commit(repo: Path, rev: str) -> str:
    try:
        return git(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").strip()
    except ChangeError:
        raise ChangeError(f"no commit {rev!r} in {repo}") from None


def default_branch(repo: Path) -> str | None:
    """The default branch as a revision (``origin/main``, ``main``, …), or None."""
    try:
        ref = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
        if ref:
            return ref.removeprefix("refs/remotes/")
    except ChangeError:
        pass
    for rev in ("origin/main", "origin/master", "main", "master"):
        try:
            git(repo, "rev-parse", "--verify", "-q", f"refs/{'remotes' if '/' in rev else 'heads'}/{rev}")
            return rev
        except ChangeError:
            continue
    return None


def merge_base(repo: Path, a: str, b: str) -> str:
    out = git(repo, "merge-base", a, b).strip()
    if not out:
        raise ChangeError(f"{a} and {b} have no common ancestor; name both ends: nolli view <base>..<head>")
    return out


def first_parent(repo: Path, sha: str) -> str | None:
    parents = git(repo, "rev-list", "--parents", "-n1", sha).split()[1:]
    return parents[0] if parents else None


def resolve(repo: Path, spec: str | None) -> Change:
    """The change that ``spec`` (see the module docstring) names in ``repo``."""
    if spec and "..." in spec:
        a, _, b = spec.partition("...")
        head = commit(repo, b or "HEAD")
        return describe(repo, Change(head=head, base=merge_base(repo, commit(repo, a or "HEAD"), head)), b or "HEAD")
    if spec and ".." in spec:
        a, _, b = spec.partition("..")
        return describe(repo, Change(head=commit(repo, b or "HEAD"), base=commit(repo, a or "HEAD")), b or "HEAD")
    rev = spec or "HEAD"
    head = commit(repo, rev)
    main = default_branch(repo)
    if main is None:
        if spec is None:
            return describe(repo, Change(head=head), rev)
        raise ChangeError(f"no default branch (origin/HEAD, main or master) to compare {spec!r} with; "
                          f"name both ends: nolli view <base>..{spec}")
    base = merge_base(repo, commit(repo, main), head)
    if base != head:
        return describe(repo, Change(head=head, base=base), rev)
    if spec is None:  # on the default branch, or nothing new on this one: the whole of HEAD
        return describe(repo, Change(head=head), rev)
    parent = first_parent(repo, head)
    if parent is None:
        return describe(repo, Change(head=head), rev)
    return describe(repo, Change(head=head, base=parent), rev)


def whole(repo: Path, rev: str = "HEAD") -> Change:
    """One commit as a whole, no change: ``nolli view --whole [<commit>]``."""
    return describe(repo, Change(head=commit(repo, rev)), rev)


def describe(repo: Path, ch: Change, rev: str) -> Change:
    """Title and description from the commits: one commit gives its subject and body; several
    give the branch's name (or the range) and the list of their subjects."""
    if not ch.is_diff:
        ch.title = git(repo, "log", "-1", "--format=%s", ch.head).strip()
        return ch
    log = git(repo, "log", "--first-parent", "--format=%H%x00%s%x00%b%x1e", f"{ch.base}..{ch.head}")
    commits = [c.strip("\n").split("\0") for c in log.split("\x1e") if c.strip()]
    if len(commits) == 1:
        _sha, subject, body = commits[0]
        ch.title, ch.description = subject, body.strip()
        return ch
    name = rev if rev not in ("HEAD", "") and not looks_like_sha(rev) else None
    if name is None:
        branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() if rev in ("HEAD", "") else ""
        name = branch if branch and branch != "HEAD" else None
    ch.title = name or f"{ch.base[:7]}..{ch.head[:7]}"
    ch.description = "\n".join(f"- {subject} ({sha[:7]})" for sha, subject, _ in reversed(commits))
    if ch.description:
        ch.description = f"{len(commits)} commits:\n\n{ch.description}"
    return ch


def looks_like_sha(rev: str) -> bool:
    return 4 <= len(rev) <= 40 and all(c in "0123456789abcdef" for c in rev.lower())


def dataset_name(repo: Path, ch: Change, pr: int | None = None) -> str:
    """``<repo>-<base7>-<head7>`` for a change (``<repo>-pr<n>-<base7>-<head7>`` for a pull
    request), ``<repo>-<head7>`` for a commit."""
    if not ch.base:
        return f"{repo.name}-{ch.head[:7]}"
    return f"{repo.name}-{f'pr{pr}-' if pr else ''}{ch.base[:7]}-{ch.head[:7]}"
