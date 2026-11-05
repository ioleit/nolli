"""Utilities to deal with plain git repositories and their changes."""

from __future__ import annotations

import io
import re
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitError(Exception):
    """git failed; the message is what it said."""


class ChangeError(GitError):
    """What the user named is no change or commit of the repository; the message says what to type instead."""


def run_git(repo: Path, *args: str, env: dict | None = None) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)
    if r.returncode:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, check=False).returncode == 0


def find_toplevel(path: Path) -> Path:
    """The root of the git working tree that ``path`` is in."""
    try:
        return Path(run_git(path, "rev-parse", "--show-toplevel").strip())
    except GitError:
        raise ChangeError(f"{path} is not in a git repository") from None


def find_commit(repo: Path, rev: str) -> str:
    """The full sha of a revision that names a commit."""
    try:
        return run_git(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").strip()
    except GitError:
        raise ChangeError(f"no commit {rev!r} in {repo}") from None


def find_head(repo: Path) -> str | None:
    try:
        return find_commit(repo, "HEAD")
    except (GitError, FileNotFoundError):
        return None


def find_remote(repo: Path, name: str = "origin") -> str | None:
    try:
        return run_git(repo, "remote", "get-url", name).strip() or None
    except GitError:
        return None


def list_tracked_files(repo: Path) -> list[Path]:
    """The files git tracks under ``repo`` (all files, for a directory that is no repository)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
        files = [Path(p.decode("utf-8", "surrogateescape")) for p in out.split(b"\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(repo) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [f for f in files if (repo / f).is_file()]


def export_commit(repo: Path, sha: str, dest: Path) -> None:
    """The tracked files of commit ``sha`` under ``dest`` (``git archive``), without touching
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
    """What ``git diff -M -U0`` says changed between two commits, by head path (base path for
    a deletion): git's status (A/M/D/R), the old path of a rename, and the hunks with the line
    numbers of every changed line."""
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
    """The default branch as a revision (``origin/main``, ``main``, …), or None."""
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
        raise ChangeError(f"{a} and {b} have no common ancestor; name both ends: nolli view <base>..<head>")
    return out


def _first_parent(repo: Path, sha: str) -> str | None:
    parents = run_git(repo, "rev-list", "--parents", "-n1", sha).split()[1:]
    return parents[0] if parents else None


def resolve(repo: Path, spec: str | None) -> ChangeSpec:
    """The change that ``spec`` names in ``repo`` (what ``nolli view`` shows):

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
    if spec and "..." in spec:
        a, _, b = spec.partition("...")
        head = find_commit(repo, b or "HEAD")
        return _describe(repo, ChangeSpec(head=head, base=merge_base(repo, find_commit(repo, a or "HEAD"), head)), b or "HEAD")
    if spec and ".." in spec:
        a, _, b = spec.partition("..")
        return _describe(repo, ChangeSpec(head=find_commit(repo, b or "HEAD"), base=find_commit(repo, a or "HEAD")), b or "HEAD")
    rev = spec or "HEAD"
    head = find_commit(repo, rev)
    main = _default_branch(repo)
    if main is None:
        if spec is None:
            return _describe(repo, ChangeSpec(head=head), rev)
        raise ChangeError(f"no default branch (origin/HEAD, main or master) to compare {spec!r} with; "
                          f"name both ends: nolli view <base>..{spec}")
    base = merge_base(repo, find_commit(repo, main), head)
    if base != head:
        return _describe(repo, ChangeSpec(head=head, base=base), rev)
    if spec is None:  # on the default branch, or nothing new on this one: the whole of HEAD
        return _describe(repo, ChangeSpec(head=head), rev)
    parent = _first_parent(repo, head)
    if parent is None:
        return _describe(repo, ChangeSpec(head=head), rev)
    return _describe(repo, ChangeSpec(head=head, base=parent), rev)


def whole(repo: Path, rev: str = "HEAD") -> ChangeSpec:
    """One commit as a whole, no change: ``nolli view --whole [<commit>]``."""
    return _describe(repo, ChangeSpec(head=find_commit(repo, rev)), rev)


def _describe(repo: Path, ch: ChangeSpec, rev: str) -> ChangeSpec:
    """Title and description from the commits: one commit gives its subject and body; several
    give the branch's name (or the range) and the list of their subjects."""
    if not ch.is_diff:
        ch.title = run_git(repo, "log", "-1", "--format=%s", ch.head).strip()
        return ch
    log = run_git(repo, "log", "--first-parent", "--format=%H%x00%s%x00%b%x1e", f"{ch.base}..{ch.head}")
    commits = [c.strip("\n").split("\0") for c in log.split("\x1e") if c.strip()]
    if len(commits) == 1:
        _sha, subject, body = commits[0]
        ch.title, ch.description = subject, body.strip()
        return ch
    name = rev if rev not in ("HEAD", "") and not _looks_like_sha(rev) else None
    if name is None:
        branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() if rev in ("HEAD", "") else ""
        name = branch if branch and branch != "HEAD" else None
    ch.title = name or f"{ch.base[:7]}..{ch.head[:7]}"
    ch.description = "\n".join(f"- {subject} ({sha[:7]})" for sha, subject, _ in reversed(commits))
    if ch.description:
        ch.description = f"{len(commits)} commits:\n\n{ch.description}"
    return ch


def _looks_like_sha(rev: str) -> bool:
    return 4 <= len(rev) <= 40 and all(c in "0123456789abcdef" for c in rev.lower())
