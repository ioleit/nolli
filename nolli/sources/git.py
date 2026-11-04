"""Plain git, the only place nolli runs it: reading commits, their files and the diff
between two of them. Every call raises ``GitError`` with what git said."""

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


def run_git(repo: Path, *args: str, env: dict | None = None) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)
    if r.returncode:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, check=False).returncode == 0


def find_toplevel(path: Path) -> Path:
    return Path(run_git(path, "rev-parse", "--show-toplevel").strip())


def find_commit(repo: Path, rev: str) -> str:
    """The full sha of a revision that names a commit."""
    return run_git(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").strip()


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
