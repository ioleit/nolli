"""Where models are kept (nolli/store/, nolli/model/keys.py) and where commits come from
(nolli/sources/), and building through them (nolli/build.py): keys, the directory store, clones fetched from a remote, and a change's model
built once, its commits' models reused."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from nolli.build import extract_commit, fetch_change, run_here
from nolli.scenes import open_built, open_change_scene
from nolli.model.keys import Repo
from nolli.model import Model
from nolli.progress import Quiet
from nolli.sources import CloneCache, FetchError, LocalRepository
from nolli.store import DirectoryStore
from nolli.store.directory import _read_key, write_change


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def src(tmp_path: Path) -> tuple[Path, list[str]]:
    """A repository with three commits in a row: (path, their shas)."""
    r = tmp_path / "src"
    (r / "shop").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    shas = []
    for i, (path, text) in enumerate([("shop/store.py", "class Store:\n    pass\n"),
                                      ("shop/cache.py", "from .store import Store\n\nclass Cache:\n    s = Store()\n"),
                                      ("shop/api.py", "from .cache import Cache\n\nC = Cache()\n")]):
        (r / path).write_text(text)
        git(r, "add", ".")
        git(r, "commit", "-q", "-m", f"step {i}")
        shas.append(git(r, "rev-parse", "HEAD"))
    return r, shas


def test_keys():
    repo = Repo.from_remote("git@github.com:IOleit/nolli.git")
    assert repo == Repo("github.com/IOleit/nolli") == Repo.from_remote("https://github.com/IOleit/nolli")
    change = repo.make_commit("a" * 40).make_change_to("b" * 40)
    assert change == repo.make_change("a" * 40, "b" * 40)
    assert change.list_sides() == (repo.make_commit("a" * 40), repo.make_commit("b" * 40))
    assert change.format_name() == "IOleit_nolli-aaaaaaa-bbbbbbb" and change.list_sides()[1].format_name() == "IOleit_nolli-bbbbbbb"
    assert repo.format_remote() == "https://github.com/IOleit/nolli"
    local = Repo("/home/me/src/shop")
    assert local.format_slug() == "shop" and local.format_remote() is None


def model(name: str) -> Model:
    return Model(meta={"repo": name})


def test_directory_store_keeps_models_changes_and_verdicts(tmp_path):
    store = DirectoryStore(tmp_path / "data")
    repo = Repo("github.com/o/r")
    c1, c2 = repo.make_commit("1" * 40), repo.make_commit("2" * 40)
    change = c1.make_change_to(c2.sha)
    assert store.load_change(change) is None and store.load_model(c1) is None and store.find_key(change.format_name()) is None
    store.save_model(c1, model("one"), {"fs": {"repo:": {"name": "one"}}})
    assert store.load_model(c1).meta["repo"] == "one" and store.load_model(c1).key == c1.format_key()
    assert store.open_maps("fs").load(c1.format_key()) == {"repo:": {"name": "one"}}
    sides = {"base": (model("base"), {}), "head": (model("head"), {"fs": {"repo:": {"name": "head"}}})}
    store.save_change(change, {"title": "a change"}, sides, {"content": {"repo:a.py": {"status": "added"}}})
    assert store.load_change(change) == {"title": "a change"}
    assert store.load_model(c2).meta["repo"] == "head"  # a change's side is a commit's model too
    assert store.load_model(c1).meta["repo"] == "one"  # the commit's own directory before the change's side
    assert store.open_maps("fs").load(c2.format_key()) == {"repo:": {"name": "head"}}
    assert store.open_maps("content").load(change.format_key()) == {"repo:a.py": {"status": "added"}}
    assert store.find_key(change.format_name()) == change and store.find_key(c1.format_name()) == c1
    assert set(store.list_names()) == {change.format_name(), c1.format_name()}
    for bad in ("", "..", "../data", ".hidden", "a/b"):
        assert store.find_key(bad) is None

    verdicts = store.open_maps("verdicts")
    assert verdicts.load(change.format_key()) == {} and verdicts.load_about(change.format_key()) is None
    kept = {"repo:x": {"verdicts": [{"id": "V1", "target": "n_x", "verdict": "good", "reason": "ok"}]}}
    verdicts.save(change.format_key(), kept, {"by": "upload"})
    store.save_change(change, {"title": "rebuilt"}, sides, {"content": {}})  # the verdicts stay with the change they answer
    assert verdicts.load(change.format_key()) == kept and verdicts.load_about(change.format_key()) == {"by": "upload"}
    assert store.load_change(change) == {"title": "rebuilt"}
    assert not [p for p in store.root.iterdir() if p.name.startswith(".")]  # no work left over
    with pytest.raises(KeyError):
        store.open_maps("verdicts").save(repo.make_commit("3" * 40).format_key(), kept)  # no model to keep them for


def test_a_model_is_stored_whole_or_not_at_all(tmp_path, monkeypatch):
    def broken(self, path):
        path.write_text("{")
        raise RuntimeError("half way")

    store = DirectoryStore(tmp_path / "data")
    key = Repo("github.com/o/r").make_commit("1" * 40)
    store.save_model(key, model("before"), {})
    monkeypatch.setattr(Model, "save", broken)
    with pytest.raises(RuntimeError):
        store.save_model(key, model("x"), {})
    assert [p.name for p in store.root.iterdir()] == [key.format_name()]  # the model kept before, no work left over
    assert store.load_model(key).meta["repo"] == "before"


def test_a_directory_written_elsewhere_is_found_by_its_name(tmp_path):
    """nolli diff -o writes a change under a name of its own; its key.json says what it is.
    A directory without one (or of another format: data from before) is not read."""
    key = Repo("github.com/o/ledger").make_change("a" * 40, "b" * 40)
    d = tmp_path / "data" / "ledger-review"
    d.mkdir(parents=True)
    write_change(d, key, {"title": "t"}, {"base": (model("ledger"), {}), "head": (model("ledger"), {})}, {})
    old = tmp_path / "data" / "old-review"
    old.mkdir()
    (old / "model.json").write_text(json.dumps({"meta": {}, "entities": [], "relations": []}))
    assert _read_key(d) == key and _read_key(old) is None
    store = DirectoryStore(tmp_path / "data")
    assert store.find_key("ledger-review") == key and store.load_change(key) == {"title": "t"}
    assert store.locate(key) == d and store.find_key("old-review") is None
    (d / "key.json").write_text(json.dumps({**json.loads((d / "key.json").read_text()), "format": 0}))
    assert _read_key(d) is None


def test_local_repository_has_its_commits(src):
    path, shas = src
    with LocalRepository(path).open_commits(Repo(str(path)), *shas) as repo:
        assert repo == path
    with pytest.raises(FetchError, match="has no commit"):
        with LocalRepository(path).open_commits(Repo(str(path)), "f" * 40):
            pass


def test_clone_cache_fetches_what_is_missing_and_holds_the_clone(src, tmp_path, monkeypatch):
    path, shas = src
    monkeypatch.setattr(Repo, "format_remote", lambda self: str(path))  # a local remote stands in for GitHub
    asked = []
    clones = CloneCache(tmp_path / "repos", token=lambda owner, repo: asked.append((owner, repo)) or None)
    repo = Repo("github.com/o/shop")
    with clones.open_commits(repo, shas[0]) as clone:
        assert clone == tmp_path / "repos/o/shop" and clones.list_held() == {clone}
        assert git(clone, "cat-file", "-t", shas[0]) == "commit"
    assert clones.list_held() == set() and asked == [("o", "shop")]
    with clones.open_commits(repo, shas[0]):  # there already: nothing fetched
        pass
    assert len(asked) == 1
    with clones.open_commits(repo, shas[0], shas[2]):
        pass
    assert len(asked) == 2


def counting(calls: list):
    def run(fn, *args, progress):
        calls.append(fn.__name__)
        return run_here(fn, *args, progress=progress)
    return run


def test_a_change_is_built_once_and_its_commits_are_reused(src, tmp_path):
    path, shas = src
    store, sources = DirectoryStore(tmp_path / "data"), LocalRepository(path)
    repo = Repo(str(path))
    calls: list[str] = []
    first = repo.make_change(shas[0], shas[1])
    about = fetch_change(store, sources, first, run=counting(calls), about={"title": "a cache", "budget": 12})
    assert calls == ["extract_commit", "extract_commit", "list_content"]
    assert about["title"] == "a cache" and about["budget"] == 12 and about["base"] == shas[0] and about["head"] == shas[1]
    sc = open_change_scene(store, first)
    assert sc.model.meta["change"]["title"] == "a cache" and sc.model.meta["change"]["base"] == shas[0]
    assert sc.find_element("repo:shop/cache.py").get("change")["status"] == "added"
    assert sorted(f.name for f in store.locate(first).iterdir()) == ["base", "change.json", "head", "key.json", "model.json", "overlays"]
    assert sorted(f.name for f in (store.locate(first) / "overlays").iterdir()) == ["change.json", "content.json", "review.json"]
    assert store.load_model(first).refs == {"base": None, "head": None}  # the merged model, each node its namesake on each side

    calls.clear()
    assert fetch_change(store, sources, first, run=counting(calls))["title"] == "a cache"
    assert calls == []  # stored: nothing built
    fetch_change(store, sources, repo.make_change(shas[1], shas[2]), run=counting(calls))
    assert calls == ["extract_commit", "list_content"]  # its base is the first change's head


def test_extract_commit_names_the_root(src):
    path, shas = src
    m, maps = extract_commit(path, shas[0], "shop", progress=Quiet())
    assert m.meta["commit"] == shas[0][:7] and m.meta["repo"] == "shop" and not m.has("repo:shop/cache.py")
    assert m.has("repo:shop/store.py")
    m.key = "shop@0"
    assert open_built(m, maps).find_element("repo:").get("files")["name"] == "shop"


def test_a_change_opened_from_what_was_kept_draws_the_same_as_one_computed(src, tmp_path):
    """What a change says about itself (change, review) and its merged model are kept at diff
    time and read back; a change kept without them (an older nolli) is computed on every read."""
    from nolli.datasets import Datasets
    from nolli.review.card import Review
    from nolli.transformations.merge import Change
    from nolli.transformations.projection import ProjectionSpec

    path, shas = src
    store, sources = DirectoryStore(tmp_path / "data"), LocalRepository(path)
    change = Repo(str(path)).make_change(shas[0], shas[2])
    fetch_change(store, sources, change, about={"title": "kept"})
    datasets = Datasets(store)
    kept = open_change_scene(store, change)
    assert type(kept.overlays["change"]).__name__ == "PrecomputedOverlay" and kept.overlays["change"].find_kept(change.format_key())
    assert store.open_maps("review").load_about(change.format_key())["overlay"] == "review"
    computed = kept.with_overlays(Change(store.open_maps("content")), Review())  # the same scene, its own overlays computing
    for preset in ("folded", "changes", "unfolded"):
        assert datasets.draw_json(kept, ProjectionSpec(preset=preset)) == datasets.draw_json(computed, ProjectionSpec(preset=preset))
    assert kept.read_all("change") == computed.read_all("change") and kept.read_all("review") == computed.read_all("review")
    assert datasets.list_changes(kept) == datasets.list_changes(computed) and datasets.list_changes(kept)

    for f in ("model.json", "overlays/change.json", "overlays/review.json"):  # a change kept by an older nolli
        (store.locate(change) / f).unlink()
    older = DirectoryStore(tmp_path / "data")
    assert older.load_model(change) is None and older.open_maps("change").load(change.format_key()) == {}
    old = open_change_scene(older, change)
    assert old.overlays["change"].find_kept(change.format_key()) is None
    assert Datasets(older).draw_json(old, ProjectionSpec(preset="changes")) == datasets.draw_json(kept, ProjectionSpec(preset="changes"))
