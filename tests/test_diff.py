"""nolli diff on a synthetic two-commit repository: structural and content changes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.cli import extract
from nolli.diff import diff

BASE = {
    "app/__init__.py": "",
    "app/store.py": "class Store:\n    def get(self, k):\n        return k\n\n\nclass Cache:\n    pass\n",
    "app/api.py": "from .store import Store\n\n\ndef handler():\n    return Store().get(1)\n",
    "app/old.py": "def unused():\n    return 0\n",
    "app/legacy_name.py": "class Thing:\n    pass\n",
}
HEAD = {
    "app/__init__.py": "",
    # Store's body edited (content only), Cache untouched, Index added
    "app/store.py": "class Store:\n    def get(self, k):\n        return k * 2\n\n\nclass Cache:\n    pass\n\n\nclass Index:\n    pass\n",
    # a new reference to Index, Store still used
    "app/api.py": "from .store import Index, Store\n\n\ndef handler():\n    Index()\n    return Store().get(1)\n",
    # app/old.py deleted; legacy_name.py renamed unchanged
    "app/thing.py": "class Thing:\n    pass\n",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(repo: Path, files: dict) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    write(tmp_path, BASE)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "app/old.py").unlink()
    git(tmp_path, "mv", "app/legacy_name.py", "app/thing.py")
    write(tmp_path, HEAD)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, base, head


def status(m, eid):
    return (m.entities[eid].attrs.get("diff") or {}).get("status")


def test_structure_and_content(repo):
    path, base, head = repo
    m, summary = diff(path, base, head, extract)
    # entities
    assert status(m, "py:app/store.py#Index") == "added"
    assert status(m, "fs:app/old.py") == "removed"
    assert status(m, "fs:app/thing.py") == "added"
    assert status(m, "fs:app/legacy_name.py") == "removed"
    assert m.entities["fs:app/legacy_name.py"].attrs["diff"]["renamed_to"] == "app/thing.py"
    assert m.entities["fs:app/thing.py"].attrs["diff"]["old_path"] == "app/legacy_name.py"
    assert "diff" not in m.entities["py:app/store.py#Cache"].attrs, "untouched class has no record"
    # content of a file and of the classes inside it
    store = m.entities["fs:app/store.py"].attrs["diff"]
    assert store["status"] == "same" and store["git"] == "M"
    assert store["added"] >= 4 and store["removed"] == 1
    assert store["hunks"] and {"old", "new", "added", "removed"} <= set(store["hunks"][0])
    s = m.entities["py:app/store.py#Store"].attrs["diff"]
    assert s["status"] == "same" and s["added"] == 1 and s["removed"] == 1 and s["added_lines"] == [3]
    # relations: a new reference api -> Index, the old file's edges gone
    added = {(r.src, r.dst) for r in m.relations if (r.attrs.get("diff") or {}).get("status") == "added"}
    assert ("fs:app/api.py", "py:app/store.py#Index") in added
    # links need both commits and the change count
    meta = m.meta["diff"]
    assert meta["base"] == base and meta["head"] == head and meta["files_changed"] == summary["files_changed"] == 4


def test_repository_untouched(repo):
    path, base, head = repo
    before = git(path, "status", "--porcelain"), git(path, "worktree", "list")
    diff(path, base, head, extract)
    assert (git(path, "status", "--porcelain"), git(path, "worktree", "list")) == before
