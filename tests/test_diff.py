"""A change of a synthetic two-commit repository: structural and content changes, the
numbered changes, details and links, comments on the drawn boxes and edges."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import build_change, draw, find_node, list_changes, open_change

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


def test_structure_and_content(repo):

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
    added = {(e.relation.src, e.relation.dst) for e in sc.list_edges() if (e.get("change") or {}).get("status") == "added"}
    assert ("repo:app/api.py", "repo:app/store.py#Index") in added
    # links need both commits and the change count
    d = sc.model.meta["change"]
    assert d["base"] == base and d["head"] == head and d["files_changed"] == 4


def test_repository_untouched(repo):
    path, base, head = repo
    before = git(path, "status", "--porcelain"), git(path, "worktree", "list")
    open_change(path, base, head)
    assert (git(path, "status", "--porcelain"), git(path, "worktree", "list")) == before


def test_scene_review(repo):
    from nolli.logic.sources import LocalRepository
    from nolli.logic.transformations.merge import Links

    path, base, head = repo
    git(path, "remote", "add", "origin", "git@github.com:acme/demo.git")
    sc = open_change(path, base, head, remote=LocalRepository(path).find_repo().format_remote(), pr=7)
    unfolded = ["repo:", "repo:app", "repo:app/store.py"]
    d = draw(sc, expanded=unfolded)
    nodes = {n["id"]: n for n in d["nodes"]}
    assert nodes["repo:app/store.py#Index"]["change"]["status"] == "added"
    store_class = nodes["repo:app/store.py#Store"]["change"]
    assert store_class["status"] == "changed" and store_class["own"] == store_class["own"] | {"status": "same", "added": 1, "removed": 1}
    assert store_class["totals"]["added"] == 0  # a class's own lines count for its file
    assert "own" not in nodes["repo:app"]["change"]  # changed only inside
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
    # the list of changes: an edited file with hunks and links into the PR's files view
    store = next(r for r in list_changes(sc) if r["id"] == "repo:app/store.py")["change"]["own"]
    assert store["git"] == "M"
    assert store["link"] == f"https://github.com/acme/demo/pull/7/files#{Links.anchor('app/store.py')}"
    assert store["hunks"] and store["hunks"][0]["link"].startswith(store["link"] + "R")
    assert {c["name"]: c["status"] for c in store["classes"]} == {"Index": "added", "Store": "same"}


def test_changes_comments_and_brief(repo):
    import json

    from nolli import operations
    from nolli.logic import comments
    from nolli.logic.formats.brief import make_brief
    from nolli.logic.transformations.merge.presets import _find_changes_cut

    path, base, head = repo
    store, key = build_change(path, base, head)
    sc = operations.open_scene(store, key)
    assert sc.find_element("repo:").get("files")["name"] == path.name
    # the changes preset draws every change as a box or edge of its own, and lists them
    rows = {r["id"]: r for r in list_changes(sc)}
    assert rows["repo:app/store.py#Index"]["change"]["status"] == "added" and rows["repo:app/store.py#Index"]["parent"] == "repo:app/store.py"
    assert rows["repo:app/old.py"]["change"]["status"] == "removed" and rows["repo:app/api.py"]["change"]["own"]["git"] == "M"
    dep = next(r for r in rows.values() if r.get("src") == "repo:app/api.py")
    assert dep["dst"] == "repo:app/store.py#Index" and dep["change"]["status"] == "added"
    assert any(f.endswith("repo:app/store.py#Index|ref") for f in dep["change"]["facts"])
    assert all(r["change"] for r in rows.values()) and rows["repo:app"]["label"]["text"] == "app"
    assert {"repo:", "repo:app", "repo:app/store.py"} <= _find_changes_cut(sc, set())
    # comments on the review diagram name its nodes and edges, by the diagram's ids or the model's; a wrong one keeps nothing
    observed = operations.make_review_key(key)
    _, errors = operations.add_comments(store, observed, json.dumps([
        {"target": "n_repo_app_store_py_Index --> n_repo_app_api_py", "verdict": "problem", "reason": "x"},
        {"target": "n_repo_nowhere_py", "verdict": "fine", "reason": "z"},
        {"target": "n_repo_app_api_py", "verdict": "maybe", "reason": ""},
    ]), "agent", verdict_needed=True)
    assert len(errors) == 4 and "the other way round" in errors[0] and "is not an element of the model" in errors[1]
    assert "must be one of" in errors[2] and "needs a reason" in errors[3] and store.load_comments(key) == []
    made, errors = operations.add_comments(store, observed, json.dumps([
        {"target": "n_repo_app_api_py --> n_repo_app_store_py_Index", "verdict": "problem", "reason": "x"},
        {"target": "n_repo_app_store_py_Index", "verdict": "question", "reason": "y"},
        {"target": "repo:app/store.py#", "verdict": "fine", "reason": "a module's own code"},
    ]), "agent", verdict_needed=True)
    assert not errors and [c["target"] for c in made] == ["use|repo:app/api.py|repo:app/store.py#Index|import",
                                                          "repo:app/store.py#Index", "repo:app/store.py#"]
    assert [c["observed"] for c in store.load_comments(key)] == [observed.format_key()] * 3  # kept on the change
    # the review panel lists them, each with what to unfold to go to it
    listed = comments.describe_comments(sc, sc.overlays["comments"].list_all(sc))
    assert listed[0]["edge"] and listed[0]["label"] == "app/api.py → app/store.py#Index"
    assert listed[0]["reveal"] == ["repo:", "repo:app", "repo:app/store.py"]
    # each shows on what stands for it in a view: on itself, or rolled up into a folded box or onto the edge that carries it
    ids = lambda x: [c["id"] for k in ("own", "inside") for c in (x.get("comments") or {}).get(k, [])]  # noqa: E731
    d = draw(sc, expanded=["repo:", "repo:app", "repo:app/store.py"])
    assert ids(find_node(d, "repo:app/store.py#Index")) == [made[1]["id"]]
    d = draw(sc, expanded=["repo:", "repo:app"])
    marked = [e for e in d["edges"] if made[0]["id"] in ids(e)]
    assert [(e["src"], e["dst"]) for e in marked] == [("repo:app/api.py", "repo:app/store.py")]
    store_box = find_node(d, "repo:app/store.py")  # Index and the module's own code are inside the folded store module
    assert ids(store_box) == [made[1]["id"], made[2]["id"]] and not store_box["comments"]["own"]
    assert "[question](verdict:question)" in store_box["hover"]["notes"] and "verdict:" not in store_box["hover"]["text"]
    d = draw(sc, expanded=["repo:"])  # app folded: the edge falls inside it, so does its comment
    assert sorted(ids(find_node(d, "repo:app"))) == sorted(c["id"] for c in made)
    md = make_brief(sc, "flowchart TD\n", "Why: speed.")
    assert "verdicts.json" in md and "source --> target" in md and "Why: speed." in md


def test_rolled_up_comments_are_counted():
    from nolli.logic.formats.descriptions.notes import describe_node

    v = lambda i, verdict: {"id": f"c-{i}", "verdict": verdict, "text": f"reason {i}"}  # noqa: E731
    # few comments: every one in full, worst first
    assert describe_node(None, [v(1, "fine")], [v(2, "problem")]) == "[problem](verdict:problem) reason 2\n\n[fine](verdict:fine) reason 1"
    # many from inside: one line counting them, like the review panel; the box's own in full
    inside = [v(2, "fine"), v(3, "blunder"), v(4, "fine"), v(5, "fine")]
    lines = describe_node(None, [v(1, "question")], inside, kind="directory").split("\n\n")
    assert lines == ["Comments in this directory: [blunder](verdict:blunder) `1` [fine](verdict:fine) `3`",
                     "[question](verdict:question) reason 1"]
    # a comment without a verdict says what it says, and is counted on its own
    assert describe_node(None, [{"id": "c-9", "text": "Why here?"}]) == "Why here?"
    assert describe_node(None, [], [{"id": "c-8", "text": "a"}, v(3, "fine"), {"id": "c-7", "text": "b"}, v(4, "fine")],
                         kind="directory") == "Comments in this directory: [fine](verdict:fine) `2`, `2` without a verdict"
