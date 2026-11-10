"""Where models are kept (nolli/store/, nolli/model/keys.py) and where commits come from
(nolli/sources/), and building through them (nolli/build.py): keys, the directory store, clones fetched from a remote, and a change's model
built once, its commits' models reused."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from nolli.build import extract_commit, fetch_change, run_here
from nolli.scenes import open_built, open_change_scene
from nolli.model.keys import ModelKey, Repo, StringKey
from nolli.model.model import Model
from nolli.progress import Quiet
from nolli.sources import CloneCache, FetchError, LocalRepository
from nolli.store import DirectoryStore, directory
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
    assert ModelKey.parse(change.format_key()) == change and ModelKey.parse(change.list_sides()[1].format_key()) == change.list_sides()[1]
    assert ModelKey.parse("picked") == StringKey("picked")
    assert directory._format_dir_name(change) == "IOleit_nolli-aaaaaaa-bbbbbbb"
    assert directory._format_dir_name(change.list_sides()[1]) == "IOleit_nolli-bbbbbbb"
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
    assert store.load_change(change) is None and store.load_model(c1) is None and store.find_key(store.locate(change).name) is None
    store.save_model(c1, model("one"), {"fs": {"repo:": {"name": "one"}}})
    assert store.load_model(c1).meta["repo"] == "one" and store.load_model(c1).key == c1
    assert store.open_maps("fs").load(c1.format_key()) == {"repo:": {"name": "one"}}
    sides = {"base": (model("base"), {}), "head": (model("head"), {"fs": {"repo:": {"name": "head"}}})}
    store.save_change(change, {"title": "a change"}, sides, {"content": {"repo:a.py": {"status": "added"}}})
    assert store.load_change(change) == {"title": "a change"}
    assert store.load_model(c2).meta["repo"] == "head"  # a change's side is a commit's model too
    assert store.load_model(c1).meta["repo"] == "one"  # the commit's own directory before the change's side
    assert store.open_maps("fs").load(c2.format_key()) == {"repo:": {"name": "head"}}
    assert store.open_maps("content").load(change.format_key()) == {"repo:a.py": {"status": "added"}}
    assert store.find_key(store.locate(change).name) == change and store.find_key(store.locate(c1).name) == c1
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
    def broken(path, model):
        path.write_text("{")
        raise RuntimeError("half way")

    store = DirectoryStore(tmp_path / "data")
    key = Repo("github.com/o/r").make_commit("1" * 40)
    store.save_model(key, model("before"), {})
    monkeypatch.setattr(directory, "_write_model", broken)
    with pytest.raises(RuntimeError):
        store.save_model(key, model("x"), {})
    assert [p for p in store.root.iterdir()] == [store.locate(key)]  # the model kept before, no work left over
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
    assert sorted(f.name for f in (store.locate(first) / "overlays").iterdir()) == ["change.json", "content.json", "names.json", "review.json"]
    assert "refs" not in json.loads((store.locate(first) / "model.json").read_text())  # union says what it refers to
    added = sc.find_element("repo:shop/cache.py")
    assert added.find_same("head") is not None and added.find_same("base") is None  # each node its namesake on each side
    assert any(el.find_same("base") and el.find_same("head") for el in sc.list_nodes())

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
    m.key = StringKey("shop@0")
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


def test_maps_read_back_from_json_are_keyed_by_addresses_and_relations():
    """A map kept as JSON reads back as it was given: nodes keyed by ``EntityId`` (so their
    methods work on keys read from disk), edges by ``Relation``."""
    from nolli.model.model import Relation
    from nolli.model.schema import EntityId, RelationKind
    from nolli.store.maps import dump_map, parse_map

    a, b = EntityId.from_path("a.py"), EntityId.from_path("b.py", "B")
    e = Relation(RelationKind.USE, a, b, "import")
    values, about = parse_map(json.loads(json.dumps(dump_map("m", {a: {"x": 1}, a.own_code(): {}, e: {"y": 2}}, {"by": "t"}))))
    assert values == {a: {"x": 1}, a.own_code(): {}, e: {"y": 2}} and about == {"by": "t"}
    assert all(type(k) is (Relation if isinstance(k, Relation) else EntityId) for k in values)
    assert next(k for k in values if k == a).path == "a.py"


@pytest.fixture
def renaming(tmp_path: Path) -> tuple[Path, str, str]:
    """A repository whose second commit renames a module (and edits it), changes a class,
    adds a module using the renamed one and removes another: (path, base, head)."""
    r = tmp_path / "renaming"
    (r / "shop").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / "shop" / "__init__.py").write_text("")
    (r / "shop" / "store.py").write_text("class Store:\n    def get(self):\n        return 1\n")
    (r / "shop" / "legacy.py").write_text("from shop.store import Store\n\n\ndef helper():\n    return Store()\n\n\nDEFAULT = helper()\n")
    (r / "shop" / "gone.py").write_text("import os\n\nX = os.getcwd()\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    base = git(r, "rev-parse", "HEAD")
    git(r, "mv", "shop/legacy.py", "shop/helpers.py")
    (r / "shop" / "helpers.py").write_text("from shop.store import Store\n\n\ndef helper():\n    return Store()\n\n\nDEFAULT = helper()\nOTHER = 2\n")
    (r / "shop" / "store.py").write_text("class Store:\n    def get(self):\n        return 2\n\n    def put(self, x):\n        return x\n")
    (r / "shop" / "api.py").write_text("from shop.helpers import helper\n\n\ndef handle():\n    return helper().get()\n")
    git(r, "rm", "-q", "shop/gone.py")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "head")
    return r, base, git(r, "rev-parse", "HEAD")


def test_a_kept_change_opened_from_disk_serves_everything_the_viewer_asks(renaming, tmp_path):
    """What ``nolli view`` does with a change: build it into a store, then, in a store opened
    afresh on the same directory (so every map is read back from JSON), draw it in every
    preset and side, list its changes, describe every box, brief the agent, and keep and
    describe verdicts. A rename is what git's per-file content says the most about."""
    from nolli.datasets import Datasets
    from nolli.model.schema import EntityId
    from nolli.transformations.projection import ProjectionSpec

    path, base, head = renaming
    change = Repo(str(path)).make_change(base, head)
    fetch_change(DirectoryStore(tmp_path / "data"), LocalRepository(path), change, about={"title": "rename"})

    store = DirectoryStore(tmp_path / "data")
    datasets = Datasets(store)
    sc = datasets.open_scene(change)
    renamed = EntityId.from_path("shop/helpers.py")
    assert sc.find_element(renamed).get("change")["old_path"] == "shop/legacy.py"

    for preset in ("folded", "changes", "unfolded", "whole"):
        for side in (None, "base", "head"):
            st = ProjectionSpec(preset=preset, **({"side": side} if side else {}))
            assert datasets.draw_json(sc, st)["nodes"]
            text, ids = datasets.draw_mermaid(sc, st)
            assert text and ids
    unfolded = datasets.draw_json(sc, ProjectionSpec(expanded=["repo:", "repo:shop"]))
    assert {n["id"] for n in unfolded["nodes"]} >= {"repo:shop/helpers.py", "repo:shop/api.py", "repo:shop/store.py"}

    items = datasets.list_changes(sc)
    assert items and any("repo:shop/helpers.py" in it["focus"] for it in items)

    brief = datasets.make_review_brief(sc)
    assert "helpers" in brief
    review_ids = [i for i in re.findall(r"\bn_\w+", datasets.make_review_diagram(sc)) if not i.endswith("_g")]
    verdicts = [{"target": t, "verdict": "good", "reason": "fine"} for t in dict.fromkeys(review_ids)]
    kept, problems = datasets.check_verdicts(sc, json.dumps(verdicts))
    assert kept and not problems, problems
    datasets.keep_verdicts(change, kept, {"by": "test"})

    again = Datasets(DirectoryStore(tmp_path / "data"))  # the verdicts read back from JSON too
    sc2 = again.open_scene(change)
    described = again.describe_verdicts(sc2)
    assert len(described) == len(kept) and all(type(x) is EntityId for v in described for x in v["ends"])
    assert again.draw_json(sc2, ProjectionSpec(preset="changes"))["nodes"]


def test_nothing_is_looked_up_for_a_key_of_no_repository(src, tmp_path):
    """A store that refuses every repository but one (as a hosted server's does): the scene a
    projection returns has a key of no repository, under which nothing is kept, so asking for
    its kept values reads nothing and answers none; a change of the allowed repository projects
    in every preset, and a key of another repository is still refused."""
    from nolli.datasets import Datasets
    from nolli.model.keys import NotAllowed
    from nolli.transformations.projection import ProjectionSpec

    path, shas = src
    change = Repo(str(path)).make_change(shas[0], shas[2])

    class Checked(DirectoryStore):
        def check(self, repo: Repo) -> None:
            if repo != change.repo:
                raise NotAllowed(f"may not read {repo.url!r}")

    store = Checked(tmp_path / "data")
    fetch_change(store, LocalRepository(path), change, about={"title": "checked"})
    datasets = Datasets(store)
    sc = datasets.open_scene(change)
    for preset in ("folded", "changes", "unfolded"):
        assert datasets.draw_json(sc, ProjectionSpec(preset=preset))["nodes"]
    assert store.list_map_names(StringKey().format_key()) == [] and store.locate_maps("", "change") is None
    with pytest.raises(NotAllowed):
        store.list_map_names(Repo("github.com/o/other").make_commit("a" * 40).format_key())
