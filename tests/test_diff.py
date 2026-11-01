"""nolli diff on a synthetic two-commit repository: structural and content changes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.build import extract
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
    assert status(m, "repo:app/store.py#Index") == "added"
    assert status(m, "repo:app/old.py") == "removed"
    assert status(m, "repo:app/thing.py") == "added"
    assert status(m, "repo:app/legacy_name.py") == "removed"
    assert m.entities["repo:app/legacy_name.py"].attrs["diff"]["renamed_to"] == "app/thing.py"
    assert m.entities["repo:app/thing.py"].attrs["diff"]["old_path"] == "app/legacy_name.py"
    assert "diff" not in m.entities["repo:app/store.py#Cache"].attrs, "untouched class has no record"
    # content of a file and of the classes inside it
    store = m.entities["repo:app/store.py"].attrs["diff"]
    assert store["status"] == "same" and store["git"] == "M"
    assert store["added"] >= 4 and store["removed"] == 1
    assert store["hunks"] and {"old", "new", "added", "removed"} <= set(store["hunks"][0])
    s = m.entities["repo:app/store.py#Store"].attrs["diff"]
    assert s["status"] == "same" and s["added"] == 1 and s["removed"] == 1 and s["added_lines"] == [3]
    # relations: a new reference api -> Index, the old file's edges gone
    added = {(r.src, r.dst) for r in m.relations if (r.attrs.get("diff") or {}).get("status") == "added"}
    assert ("repo:app/api.py", "repo:app/store.py#Index") in added
    # links need both commits and the change count
    meta = m.meta["diff"]
    assert meta["base"] == base and meta["head"] == head and meta["files_changed"] == summary["files_changed"] == 4


def test_repository_untouched(repo):
    path, base, head = repo
    before = git(path, "status", "--porcelain"), git(path, "worktree", "list")
    diff(path, base, head, extract)
    assert (git(path, "status", "--porcelain"), git(path, "worktree", "list")) == before


def test_scene_review(repo):
    from nolli.changes import Links, details
    from nolli.scene import SceneState, scene

    path, base, head = repo
    git(path, "remote", "add", "origin", "git@github.com:acme/demo.git")
    m, _ = diff(path, base, head, extract)
    m.meta["diff"]["pr"] = 7
    sc = scene(m, SceneState(expanded=["repo:", "repo:app", "repo:app/store.py"]))
    nodes = {n["id"]: n for n in sc["nodes"]}
    assert nodes["repo:app/store.py#Index"]["diff"]["status"] == "added"
    assert nodes["repo:app/store.py#Store"]["diff"] == nodes["repo:app/store.py#Store"]["diff"] | {"status": "changed", "added": 1, "removed": 1}
    assert nodes["repo:app/old.py"]["diff"]["status"] == "removed"
    assert nodes["repo:app/store.py#Cache"]["diff"] is None
    edges = {(e["src"], e["dst"]): e for e in sc["edges"]}
    new = edges[("repo:app/api.py", "repo:app/store.py#Index")]["diff"]
    assert new["status"] == "added" and new["base"] == 0 and new["head"] >= 1 and new["changes"][0]["status"] == "added"
    assert sc["diff"]["link"] == "https://github.com/acme/demo/pull/7"
    # one commit at a time: what is missing on that side is not drawn
    ids = lambda st: {n["id"] for n in scene(m, SceneState(expanded=["repo:", "repo:app", "repo:app/store.py"], side=st))["nodes"]}
    assert "repo:app/store.py#Index" not in ids("base") and "repo:app/old.py" in ids("base")
    assert "repo:app/store.py#Index" in ids("head") and "repo:app/old.py" not in ids("head")
    # the default view unfolds towards the change
    assert "repo:app" in scene(m, SceneState())["state"]["expanded"]
    # details: files with hunks and links into the PR's files view
    d = details(m, "repo:app")
    store = next(f for f in d["files"] if f["path"] == "app/store.py")
    assert store["link"] == f"https://github.com/acme/demo/pull/7/files#{Links.anchor('app/store.py')}"
    assert store["hunks"] and store["hunks"][0]["link"].startswith(store["link"] + "R")
    assert any(c["name"] == "Store" for c in d["classes"])
    assert any(e["id"] == "repo:app/store.py#Index" and e["status"] == "added" for e in d["entities"])


def test_items_verdicts_and_export(repo):
    import json

    from nolli.changes import change_items
    from nolli.review import read_verdicts, resolve_verdicts, review_expanded, review_markdown
    from nolli.scene import SceneState, scene

    path, base, head = repo
    m, _ = diff(path, base, head, extract)
    assert m.entities["repo:"].name == path.name
    items = change_items(m)
    by_title = {it["title"]: it for it in items}
    assert [it["id"] for it in items] == [f"C{i}" for i in range(1, len(items) + 1)]
    assert "new class app/store.py#Index" in by_title
    dep = by_title["app/api.py → app/store.py: dependency changed"]
    assert any(f.endswith("repo:app/store.py#Index|ref") for f in dep["facts"]) and dep["reveal"] == ["repo:", "repo:app"]
    assert any(it["category"] == "content" and it["title"].startswith("edited app/store.py") for it in items)
    assert "repo:app" in review_expanded(m)
    # verdicts name the diagram's nodes and edges; each lands on what stands for it in the view
    v = read_verdicts(json.dumps([
        {"target": "n_repo_app_api_py --> n_repo_app_store_py", "verdict": "problem", "reason": "x"},
        {"target": "n_repo_app_store_py_Index", "verdict": "question", "reason": "y"},
        {"target": "n_repo_nowhere_py", "verdict": "fine", "reason": "z"},
        {"target": "n_repo_app_g --> n_repo_app_store_py", "verdict": "fine", "reason": "an unfolded box is a subgraph"},
    ]))
    assert [x["id"] for x in v] == ["V1", "V2", "V3", "V4"]
    assert resolve_verdicts(m, v)[3]["ends"] == ["repo:app", "repo:app/store.py"]
    sc = scene(m, SceneState(expanded=["repo:", "repo:app"]), v)
    marked = [e for e in sc["edges"] if "V1" in e["verdicts"]]
    assert len(marked) == 1 and marked[0]["verdict"] == "problem" and (marked[0]["src"], marked[0]["dst"]) == ("repo:app/api.py", "repo:app/store.py")
    store = next(n for n in sc["nodes"] if n["id"] == "repo:app/store.py")  # Index is inside the folded store module
    assert store["verdicts"] == ["V2"] and store["verdict"] == "question"
    assert "[question](verdict:question)" in store["review"]
    assert "verdict:" not in store["info"]
    assert not any("V3" in x.get("verdicts", []) for x in sc["nodes"] + sc["edges"])  # an unknown target is not drawn
    sc = scene(m, SceneState(expanded=["repo:"]), v)  # app folded: the edge falls inside it, so does the verdict
    app = next(n for n in sc["nodes"] if n["id"] == "repo:app")
    assert set(app["verdicts"]) == {"V1", "V2", "V4"} and app["verdict"] == "problem"
    with pytest.raises(ValueError):
        read_verdicts('[{"target": "n_repo_app_api_py", "verdict": "maybe"}]')
    with pytest.raises(ValueError):
        read_verdicts('[{"id": "C1", "verdict": "fine"}]')
    md = review_markdown(m, "flowchart TD\n", "Why: speed.")
    assert "verdicts.json" in md and "source --> target" in md and "Why: speed." in md


def test_rolled_up_verdicts_are_counted():
    from nolli.review import node_review_info

    v = lambda i, verdict: {"id": f"V{i}", "verdict": verdict, "reason": f"reason {i}"}  # noqa: E731
    # few verdicts: every reason in full, worst first
    assert node_review_info(None, [v(1, "fine")], [v(2, "problem")]) == "[problem](verdict:problem) reason 2\n\n[fine](verdict:fine) reason 1"
    # many from inside: one line counting them, like the review panel; the box's own in full
    inside = [v(2, "fine"), v(3, "blunder"), v(4, "fine"), v(5, "fine")]
    lines = node_review_info(None, [v(1, "question")], inside, items=13, kind="directory").split("\n\n")
    assert lines == ["`13 changes` in this directory · verdicts: [blunder](verdict:blunder) `1` [fine](verdict:fine) `3`",
                     "[question](verdict:question) reason 1"]
