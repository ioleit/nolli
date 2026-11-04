"""A change of a synthetic two-commit repository: structural and content changes, the
numbered changes, details and links, verdicts on the drawn boxes and edges."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helpers import draw, find_node, open_change

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


def change_of(sc, x):
    return sc.find_element(x).get("change")


def status(sc, x):
    return (change_of(sc, x) or {}).get("status")


def keep(sc, verdicts):
    """Verdicts kept as the change's review, as save_verdicts keeps them in a store."""
    from nolli.overlays.verdicts import dump_verdicts

    sc.overlays["verdicts"].stored.save(sc.model.key, dump_verdicts(verdicts), {"by": "test"})


def test_structure_and_content(repo):
    from nolli.overlays.change import summarize_change

    path, base, head = repo
    sc = open_change(path, base, head)
    # nodes
    assert status(sc, "repo:app/store.py#Index") == "added"
    assert status(sc, "repo:app/old.py") == "removed"
    assert status(sc, "repo:app/thing.py") == "added"
    assert status(sc, "repo:app/legacy_name.py") == "removed"
    assert change_of(sc, "repo:app/legacy_name.py")["renamed_to"] == "app/thing.py"
    assert change_of(sc, "repo:app/thing.py")["old_path"] == "app/legacy_name.py"
    assert change_of(sc, "repo:app/store.py#Cache") is None, "untouched class has no record"
    # content of a file and of the classes inside it
    store = change_of(sc, "repo:app/store.py")
    assert store["status"] == "same" and store["git"] == "M"
    assert store["added"] >= 4 and store["removed"] == 1
    assert store["hunks"] and {"old", "new", "added", "removed"} <= set(store["hunks"][0])
    s = change_of(sc, "repo:app/store.py#Store")
    assert s["status"] == "same" and s["added"] == 1 and s["removed"] == 1 and s["added_lines"] == [3]
    # edges: a new reference api -> Index
    added = {(e.key.src, e.key.dst) for e in sc.list_edges() if (e.get("change") or {}).get("status") == "added"}
    assert ("repo:app/api.py", "repo:app/store.py#Index") in added
    # links need both commits and the change count
    d = summarize_change(sc)
    assert d["base"] == base and d["head"] == head and d["files_changed"] == 4


def test_repository_untouched(repo):
    path, base, head = repo
    before = git(path, "status", "--porcelain"), git(path, "worktree", "list")
    open_change(path, base, head)
    assert (git(path, "status", "--porcelain"), git(path, "worktree", "list")) == before


def test_scene_review(repo):
    from nolli.cli import find_repo
    from nolli.overlays.change import Links, detail_box

    path, base, head = repo
    git(path, "remote", "add", "origin", "git@github.com:acme/demo.git")
    sc = open_change(path, base, head, remote=find_repo(path).format_remote(), pr=7)
    unfolded = ["repo:", "repo:app", "repo:app/store.py"]
    d = draw(sc, expanded=unfolded)
    nodes = {n["id"]: n for n in d["nodes"]}
    assert nodes["repo:app/store.py#Index"]["change"]["status"] == "added"
    assert nodes["repo:app/store.py#Store"]["change"] == nodes["repo:app/store.py#Store"]["change"] | {"status": "changed", "added": 1, "removed": 1}
    assert nodes["repo:app/old.py"]["change"]["status"] == "removed"
    assert "change" not in nodes["repo:app/store.py#Cache"]
    edges = {(e["src"], e["dst"]): e for e in d["edges"]}
    new = edges[("repo:app/api.py", "repo:app/store.py#Index")]["change"]
    assert new["status"] == "added" and new["base"] == 0 and new["head"] >= 1 and new["changes"][0]["status"] == "added"
    assert d["diff"]["link"] == "https://github.com/acme/demo/pull/7"
    # one commit at a time: what is missing on that side is not drawn
    ids = lambda side: {n["id"] for n in draw(sc, expanded=unfolded, side=side)["nodes"]}  # noqa: E731
    assert "repo:app/store.py#Index" not in ids("base") and "repo:app/old.py" in ids("base")
    assert "repo:app/store.py#Index" in ids("head") and "repo:app/old.py" not in ids("head")
    # the default view unfolds towards the change
    assert "repo:app" in draw(sc)["state"]["expanded"]
    # details: files with hunks and links into the PR's files view
    det = detail_box(sc, "repo:app")
    store = next(f for f in det["files"] if f["path"] == "app/store.py")
    assert store["link"] == f"https://github.com/acme/demo/pull/7/files#{Links.anchor('app/store.py')}"
    assert store["hunks"] and store["hunks"][0]["link"].startswith(store["link"] + "R")
    assert any(c["name"] == "Store" for c in det["classes"])
    assert any(e["id"] == "repo:app/store.py#Index" and e["status"] == "added" for e in det["entities"])


def test_items_verdicts_and_export(repo):
    import json

    from nolli.export.brief import make_brief
    from nolli.overlays.change import list_changes
    from nolli.overlays.verdicts import find_diagram_ids, find_review_cut, read_verdicts

    path, base, head = repo
    sc = open_change(path, base, head)
    assert sc.find_element("repo:").get("facts")["name"] == path.name
    items = list_changes(sc)
    by_title = {it["title"]: it for it in items}
    assert [it["id"] for it in items] == [f"C{i}" for i in range(1, len(items) + 1)]
    assert "new class app/store.py#Index" in by_title
    dep = by_title["app/api.py → app/store.py: dependency changed"]
    assert any(f.endswith("repo:app/store.py#Index|ref") for f in dep["facts"]) and dep["reveal"] == ["repo:", "repo:app"]
    assert any(it["category"] == "content" and it["title"].startswith("edited app/store.py") for it in items)
    assert "repo:app" in find_review_cut(sc)
    # verdicts name the diagram's nodes and edges; each lands on what stands for it in the view
    ids = find_diagram_ids(sc)
    v = read_verdicts(json.dumps([
        {"target": "n_repo_app_api_py --> n_repo_app_store_py", "verdict": "problem", "reason": "x"},
        {"target": "n_repo_app_store_py_Index", "verdict": "question", "reason": "y"},
        {"target": "n_repo_nowhere_py", "verdict": "fine", "reason": "z"},
        {"target": "n_repo_app_g --> n_repo_app_store_py", "verdict": "fine", "reason": "an unfolded box is a subgraph"},
    ]), ids)
    assert [x["id"] for x in v] == ["V1", "V2", "V3", "V4"]
    assert v[3]["ends"] == ["repo:app", "repo:app/store.py"] and v[2]["ends"] == []
    keep(sc, v)
    d = draw(sc, expanded=["repo:", "repo:app"])
    verdicts = lambda x: x.get("verdicts") or {}  # noqa: E731
    marked = [e for e in d["edges"] if "V1" in verdicts(e).get("verdicts", [])]
    assert len(marked) == 1 and marked[0]["verdicts"]["verdict"] == "problem"
    assert (marked[0]["src"], marked[0]["dst"]) == ("repo:app/api.py", "repo:app/store.py")
    store = find_node(d, "repo:app/store.py")  # Index is inside the folded store module
    assert store["verdicts"]["verdicts"] == ["V2"] and store["verdicts"]["verdict"] == "question"
    assert "[question](verdict:question)" in store["review"]["card"]
    assert "verdict:" not in store["hover"]["text"]
    assert not any("V3" in verdicts(x).get("verdicts", []) for x in d["nodes"] + d["edges"])  # an unknown target is not drawn
    d = draw(sc, expanded=["repo:"])  # app folded: the edge falls inside it, so does the verdict
    app = find_node(d, "repo:app")
    assert set(app["verdicts"]["verdicts"]) == {"V1", "V2", "V4"} and app["verdicts"]["verdict"] == "problem"
    with pytest.raises(ValueError):
        read_verdicts('[{"target": "n_repo_app_api_py", "verdict": "maybe"}]', ids)
    with pytest.raises(ValueError):
        read_verdicts('[{"id": "C1", "verdict": "fine"}]', ids)
    md = make_brief(sc, "flowchart TD\n", "Why: speed.")
    assert "verdicts.json" in md and "source --> target" in md and "Why: speed." in md


def test_rolled_up_verdicts_are_counted():
    from nolli.overlays.review import describe_box

    v = lambda i, verdict: {"id": f"V{i}", "verdict": verdict, "reason": f"reason {i}"}  # noqa: E731
    # few verdicts: every reason in full, worst first
    assert describe_box(None, [v(1, "fine")], [v(2, "problem")]) == "[problem](verdict:problem) reason 2\n\n[fine](verdict:fine) reason 1"
    # many from inside: one line counting them, like the review panel; the box's own in full
    inside = [v(2, "fine"), v(3, "blunder"), v(4, "fine"), v(5, "fine")]
    lines = describe_box(None, [v(1, "question")], inside, items=13, kind="directory").split("\n\n")
    assert lines == ["`13 changes` in this directory · verdicts: [blunder](verdict:blunder) `1` [fine](verdict:fine) `3`",
                     "[question](verdict:question) reason 1"]
