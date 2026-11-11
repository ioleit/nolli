"""Utilities to deal with plain git repositories and their changes."""

from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitError(Exception):
    """git failed; the message is what it said."""


class ChangeError(GitError):
    """What the user named is no change or commit of the repository; the message says what to type instead."""


def run_git(repo: Path, *args: str, env: dict | None = None) -> str:
    """git's output for ``args`` in ``repo``; raises GitError with what git said."""
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)
    if r.returncode:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, check=False).returncode == 0


def find_toplevel(path: Path) -> Path:
    """Return the root of the git working tree that ``path`` is in; raise ChangeError if it is in none."""
    try:
        return Path(run_git(path, "rev-parse", "--show-toplevel").strip())
    except GitError:
        raise ChangeError(f"{path} is not in a git repository") from None


def _find_commit(repo: Path, rev: str) -> str:
    """Return the full sha of the commit a revision names; raise ChangeError if it names none."""
    try:
        return run_git(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").strip()
    except GitError:
        raise ChangeError(f"no commit {rev!r} in {repo}") from None


def find_head(repo: Path) -> str | None:
    try:
        return _find_commit(repo, "HEAD")
    except (GitError, FileNotFoundError):
        return None


def find_remote(repo: Path, name: str = "origin") -> str | None:
    try:
        return run_git(repo, "remote", "get-url", name).strip() or None
    except GitError:
        return None


def list_tracked_files(repo: Path) -> list[Path]:
    """List the files git tracks under ``repo``, or all files if the directory is no repository."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
        files = [Path(p.decode("utf-8", "surrogateescape")) for p in out.split(b"\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(repo) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [f for f in files if (repo / f).is_file()]


def export_commit(repo: Path, sha: str, dest: Path) -> None:
    """Write the tracked files of commit ``sha`` under ``dest`` with ``git archive``, without touching
    the repository."""
    data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")


@dataclass
class FileChange:
    git: str  # A, M, D, R
    path: str  # head path (base path for deletions)
    old_path: str | None = None  # base path, for renames
    binary: bool = False
    hunks: list[dict] = field(default_factory=list)  # {old: [start, len], new: [start, len], removed: [lines], added: [lines]}

    @property
    def added(self) -> int:
        return sum(len(h["added"]) for h in self.hunks)

    @property
    def removed(self) -> int:
        return sum(len(h["removed"]) for h in self.hunks)

    def to_dict(self) -> dict:
        out = {"git": self.git, "added": self.added, "removed": self.removed, "hunks": self.hunks}
        if self.old_path:
            out["old_path"] = self.old_path
        if self.binary:
            out["binary"] = True
        return out


def list_file_changes(repo: Path, base: str, head: str) -> dict[str, FileChange]:
    """Return what ``git diff -M -U0`` says changed between two commits, keyed by head path (base
    path for a deletion). Each entry has git's status (A/M/D/R), the old path of a rename, and the
    hunks with the line numbers of every changed line."""
    out = run_git(repo, "diff", "-M", "-U0", "--no-color", "--no-ext-diff", base, head)
    changes: dict[str, FileChange] = {}
    cur: FileChange | None = None
    a_path = ""
    hunk: dict | None = None
    old_line = new_line = 0
    for line in out.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"^diff --git a/(.*) b/(.*)$", line)
            a_path, b = (m.group(1), m.group(2)) if m else (line, line)
            cur = changes[b] = FileChange("M", b)
            hunk = None
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.git = "A"
        elif line.startswith("deleted file mode"):
            cur.git = "D"
            changes.pop(cur.path, None)
            cur.path = a_path
            changes[cur.path] = cur
        elif line.startswith("rename from "):
            cur.git = "R"
            cur.old_path = line[len("rename from "):]
        elif line.startswith("Binary files "):
            cur.binary = True
        elif line.startswith("@@"):
            m = HUNK.match(line)
            if not m:
                continue
            os_, ol, ns, nl = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
            hunk = {"old": [os_, ol], "new": [ns, nl], "removed": [], "added": []}
            cur.hunks.append(hunk)
            old_line, new_line = os_, ns
        elif hunk is not None and line.startswith("-") and not line.startswith("---"):
            hunk["removed"].append(old_line)
            old_line += 1
        elif hunk is not None and line.startswith("+") and not line.startswith("+++"):
            hunk["added"].append(new_line)
            new_line += 1
    return changes


# -- a change, as the user names it ---------------------------------------------


@dataclass
class ChangeSpec:
    head: str                  # full sha
    base: str | None = None    # full sha; None: the head commit as a whole, no change
    title: str = ""            # what the viewer's header calls it
    description: str = ""      # what review.md quotes as the change's description (markdown)

    @property
    def is_diff(self) -> bool:
        return self.base is not None


def _default_branch(repo: Path) -> str | None:
    """Return the default branch as a revision (``origin/main``, ``main``, …), or None if there is none."""
    try:
        ref = run_git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
        if ref:
            return ref.removeprefix("refs/remotes/")
    except GitError:
        pass
    for rev in ("origin/main", "origin/master", "main", "master"):
        try:
            run_git(repo, "rev-parse", "--verify", "-q", f"refs/{'remotes' if '/' in rev else 'heads'}/{rev}")
            return rev
        except GitError:
            continue
    return None


def merge_base(repo: Path, a: str, b: str) -> str:
    try:
        out = run_git(repo, "merge-base", a, b).strip()
    except GitError:
        out = ""
    if not out:
        raise ChangeError(f"{a[:7]} and {b[:7]} have no common ancestor; name both ends: <base>..<head>")
    return out


def _first_parent(repo: Path, sha: str) -> str | None:
    parents = run_git(repo, "rev-list", "--parents", "-n1", sha).split()[1:]
    return parents[0] if parents else None


WORKTREE = "WORKTREE"  # the revision that names the files on disk (_snapshot_worktree)


def _find_point(repo: Path, rev: str) -> str:
    """Return the full sha of a revision, or of the working tree's snapshot for ``WORKTREE``."""
    return _snapshot_worktree(repo) if rev == WORKTREE else _find_commit(repo, rev)


def _snapshot_worktree(repo: Path) -> str:
    """Commit the files on disk and return the commit's sha. The files are HEAD plus uncommitted
    edits, staged or not, and untracked files that are not ignored. The commit is written to the
    repository's objects only (no branch, ref or index changes; git prunes it as any unreferenced
    object), with fixed author, date and message, so the same files on the same HEAD give the
    same sha. Without edits it is HEAD itself."""
    if subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", WORKTREE], capture_output=True, check=False).returncode == 0:
        raise ChangeError(f"{repo} has a branch or tag called {WORKTREE}, which nolli reads as the files on disk; "
                          f"rename it, or name it as refs/heads/{WORKTREE}")
    head = find_head(repo)
    with tempfile.TemporaryDirectory(prefix="nolli-index-") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        run_git(repo, "read-tree", *([head] if head else ["--empty"]), env=env)
        run_git(repo, "add", "-A", "--", ".", env=env)
        tree = run_git(repo, "write-tree", env=env).strip()
    if head and tree == run_git(repo, "rev-parse", f"{head}^{{tree}}").strip():
        return head
    date = run_git(repo, "log", "-1", "--format=%cI", head).strip() if head else "1970-01-01T00:00:00Z"
    env = {**os.environ, "GIT_AUTHOR_NAME": "nolli", "GIT_AUTHOR_EMAIL": "nolli@localhost", "GIT_AUTHOR_DATE": date,
           "GIT_COMMITTER_NAME": "nolli", "GIT_COMMITTER_EMAIL": "nolli@localhost", "GIT_COMMITTER_DATE": date}
    return run_git(repo, "commit-tree", tree, *(["-p", head] if head else []), "-m", UNCOMMITTED, env=env).strip()


UNCOMMITTED = "Uncommitted edits"  # the subject of a working tree's snapshot


def _resolve_snapshot(repo: Path, rev: str) -> ChangeSpec:
    """Resolve one commit (or the working tree) as a whole: the target ``R`` or ``WORKTREE``."""
    return _describe(repo, ChangeSpec(head=_find_point(repo, rev)), rev)


def _resolve_range(repo: Path, a: str | None, b: str | None, symmetric: bool) -> ChangeSpec:
    """Resolve ``A..B`` (exactly from A to B) or, with ``symmetric``, ``A...B`` (from where they
    diverged to B). An end left out is HEAD, except a left-out A of ``...B``, which is the default
    branch. Raise ChangeError for a change with nothing in it."""
    head = _find_point(repo, b or "HEAD")
    if symmetric:
        left = a or _default_branch(repo)
        if left is None:
            raise ChangeError(f"no default branch (origin/HEAD, main or master) to compare {b or 'HEAD'} with; "
                              f"name both ends: <base>...{b or 'HEAD'}")
        base = merge_base(repo, _find_point(repo, left), head)
    else:
        base = _find_point(repo, a or "HEAD")
    if base == head:
        what = f"{a or ''}{'...' if symmetric else '..'}{b or ''}"
        hint = f"; {b or 'HEAD'} is on {left}: {b or 'HEAD'}^! is its own change" if symmetric and not a else ""
        raise ChangeError(f"{what} is no change (both ends are {head[:7]}){hint}")
    return _describe(repo, ChangeSpec(head=head, base=base), b or "HEAD")


def _resolve_own(repo: Path, rev: str) -> ChangeSpec:
    """Resolve ``R^!``: commit R against its first parent."""
    head = _find_point(repo, rev)
    parent = _first_parent(repo, head)
    if parent is None:
        raise ChangeError(f"{rev} has no parent; build it as a snapshot: {rev}")
    return _describe(repo, ChangeSpec(head=head, base=parent), rev)


def _resolve_adds(repo: Path, rev: str) -> ChangeSpec:
    """Return the change that revision R adds, running from where R left the default branch to R.
    If R is on the default branch already (a commit, or a merged pull request's merge commit),
    compare R with its first parent; take a root commit as a whole. Raise ChangeError if there
    is no default branch."""
    head = _find_point(repo, rev)
    main = _default_branch(repo)
    if main is None:
        raise ChangeError(f"no default branch (origin/HEAD, main or master) to compare {rev!r} with; name both ends: <base>...{rev}")
    base = merge_base(repo, _find_commit(repo, main), head)
    if base != head:
        return _describe(repo, ChangeSpec(head=head, base=base), rev)
    parent = _first_parent(repo, head)
    return _describe(repo, ChangeSpec(head=head, base=parent), rev)


def _describe(repo: Path, ch: ChangeSpec, rev: str) -> ChangeSpec:
    """Return ``ch`` with its title and description taken from its commits (``rev`` is what the user
    called its head): one commit gives its subject and body; several give the branch's name (or
    the range) and the list of their subjects."""
    if not ch.is_diff:
        ch.title = run_git(repo, "log", "-1", "--format=%s", ch.head).strip()
        return ch
    log = run_git(repo, "log", "--first-parent", "--format=%H%x00%s%x00%b%x1e", f"{ch.base}..{ch.head}")
    commits = [c.strip("\n").split("\0") for c in log.split("\x1e") if c.strip()]
    if len(commits) == 1:
        _sha, subject, body = commits[0]
        ch.title, ch.description = subject, body.strip()
        return ch
    name = rev if rev not in ("HEAD", "", WORKTREE) and not _looks_like_sha(rev) else None
    if name is None:
        branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() if rev in ("HEAD", "", WORKTREE) else ""
        name = branch if branch and branch != "HEAD" else None
    ch.title = name or f"{ch.base[:7]}..{ch.head[:7]}"
    if rev == WORKTREE and ch.title != UNCOMMITTED:
        ch.title += f" with {UNCOMMITTED.lower()}"
    ch.description = "\n".join(f"- {subject} ({sha[:7]})" for sha, subject, _ in reversed(commits))
    if ch.description:
        ch.description = f"{len(commits)} commits:\n\n{ch.description}"
    return ch


def _looks_like_sha(rev: str) -> bool:
    return 4 <= len(rev) <= 40 and all(c in "0123456789abcdef" for c in rev.lower())


@dataclass(frozen=True)
class Revs:
    """Commits named as git names them, not looked up: ``snapshot`` (``R``: one commit, or
    ``WORKTREE``), ``range`` (``A..B``; ``A...B``, ``symmetric``, from where B left A; either
    end left out) or ``own`` (``R^!``: what R itself changed)."""
    kind: str
    head: str | None = None
    base: str | None = None
    symmetric: bool = False


def parse_revs(text: str) -> Revs:
    """Parse ``text`` as git's revisions, without looking them up."""
    if "..." in text:
        base, _, head = text.partition("...")
        return Revs("range", head=head or None, base=base or None, symmetric=True)
    if ".." in text:
        base, _, head = text.partition("..")
        return Revs("range", head=head or None, base=base or None)
    if text.endswith("^!"):
        return Revs("own", head=text[:-2])
    return Revs("snapshot", head=text)


def resolve_revs(repo: Path, revs: Revs, adds: bool = False) -> ChangeSpec:
    """Resolve ``revs`` in ``repo`` and return the change or snapshot they name. With ``adds``, a
    single revision R means what R adds (``_resolve_adds``), not R as a whole. Raise ChangeError."""
    if revs.kind == "snapshot" and adds and revs.head != WORKTREE:
        return _resolve_adds(repo, revs.head)
    if revs.kind == "snapshot":
        return _resolve_snapshot(repo, revs.head)
    if revs.kind == "own":
        return _resolve_own(repo, revs.head)
    return _resolve_range(repo, revs.base, revs.head, revs.symmetric)
