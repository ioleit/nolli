"""Where models are kept (nolli/store/, nolli/model/keys.py) and where commits come from
(nolli/logic/sources/), and building through them (nolli/operations.py): keys, clones fetched
from a remote, and a change's model built once, its commits' models reused. nolli's own store
itself: tests/test_repo_store.py."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from helpers import list_changes, open_extracted

from nolli.operations import _extract_commit, _fetch_change, _run_here
from nolli.logic import transformations
from nolli.logic.formats import mermaid, viewer
from nolli.logic.sources import CloneCache, FetchError, LocalRepository
from nolli.logic.transformations import projection
from nolli.model.keys import ModelKey, Repo, StringKey
from nolli.progress import Quiet
from nolli.logic.transformations.merge import presets as changes
from nolli import operations
from nolli.logic import comments
from nolli.logic.formats import brief
from nolli.operations import _open_change_scene, open_scene
from nolli.store import RepoStoreProvider


def open_store(tmp_path: Path, repo: Repo):
    """The store of ``repo``'s models under <tmp>/data, opened afresh (every file read back from JSON)."""
    return RepoStoreProvider(tmp_path / "data").open(repo)


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
    assert repo.format_remote() == "https://github.com/IOleit/nolli"
    local = Repo("/home/me/src/shop")
    assert local.format_slug() == "shop" and local.format_remote() is None


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
        return _run_here(fn, *args, progress=progress)
    return run


def test_a_change_is_built_once_and_its_commits_are_reused(src, tmp_path):
    path, shas = src
    repo = Repo(str(path))
    store, sources = open_store(tmp_path, repo), LocalRepository(path)
    calls: list[str] = []
    first = repo.make_change(shas[0], shas[1])
    about = _fetch_change(store, sources, first, run=counting(calls), about={"title": "a cache", "budget": 12})
    assert calls == ["_extract_commit", "_extract_commit", "_list_content"]
    assert about["title"] == "a cache" and about["budget"] == 12 and about["base"] == shas[0] and about["head"] == shas[1]
    sc = _open_change_scene(store, first)
    assert sc.model.meta["change"]["title"] == "a cache" and sc.model.meta["change"]["base"] == shas[0]
    assert sc.find_element("repo:shop/cache.py").get("change")["status"] == "added"
    assert sorted(f.name for f in store._locate(first).iterdir()) == ["change.json", "key.json", "model.json", "overlays"]
    assert sorted(f.name for f in (store._locate(first) / "overlays").iterdir()) == ["change.json", "content.json", "names.json"]
    assert "refs" not in json.loads((store._locate(first) / "model.json").read_text())  # union says what it refers to
    added = sc.find_element("repo:shop/cache.py")
    assert added.find_same("head") is not None and added.find_same("base") is None  # each node its namesake on each side
    assert any(el.find_same("base") and el.find_same("head") for el in sc.list_nodes())

    calls.clear()
    assert _fetch_change(store, sources, first, run=counting(calls))["title"] == "a cache"
    assert calls == []  # stored: nothing built
    _fetch_change(store, sources, repo.make_change(shas[1], shas[2]), run=counting(calls))
    assert calls == ["_extract_commit", "_list_content"]  # its base is the first change's head


def test_extract_commit_names_the_root(src):
    path, shas = src
    m, maps = _extract_commit(path, shas[0], "shop", progress=Quiet())
    assert m.meta["commit"] == shas[0][:7] and m.meta["repo"] == "shop" and not m.has("repo:shop/cache.py")
    assert m.has("repo:shop/store.py")
    m.key = StringKey("shop")
    assert open_extracted(m, maps).find_element("repo:").get("files")["name"] == "shop"



def _draw(scene, spec):
    """``scene`` projected as ``spec`` asks."""
    return transformations.apply(scene, projection.fold(spec))


def test_a_change_opened_from_what_was_kept_draws_the_same_as_one_computed(src, tmp_path):
    """What a change says about itself (change) and its merged model are kept at diff
    time and read back; a change kept without them (an older nolli) is computed on every read."""
    from helpers import spec

    from nolli.logic.transformations.merge.change import Change

    path, shas = src
    change = Repo(str(path)).make_change(shas[0], shas[2])
    store, sources = open_store(tmp_path, change.repo), LocalRepository(path)
    _fetch_change(store, sources, change, about={"title": "kept"})
    kept = _open_change_scene(store, change)
    assert type(kept.overlays["change"]).__name__ == "PrecomputedOverlay" and kept.overlays["change"].find_kept(change.format_key())
    computed = kept.with_overlays(Change(store.open_maps("content")))  # the same scene, its own overlays computing
    for preset in ("folded", "changes", "unfolded"):
        assert viewer.export_scene(_draw(kept, spec(preset=preset))) == viewer.export_scene(_draw(computed, spec(preset=preset)))
    assert kept.read_all("change") == computed.read_all("change")
    assert list_changes(kept) == list_changes(computed) and list_changes(kept)

    for f in ("model.json", "overlays/change.json"):  # a change kept by an older nolli
        (store._locate(change) / f).unlink()
    older = open_store(tmp_path, change.repo)
    assert older.load_model(change) is None and older.open_maps("change").load(change.format_key()) == {}
    old = _open_change_scene(older, change)
    assert old.overlays["change"].find_kept(change.format_key()) is None
    assert viewer.export_scene(_draw(old, spec(preset="changes"))) == viewer.export_scene(_draw(kept, spec(preset="changes")))


def test_maps_read_back_from_json_are_keyed_by_addresses_and_relations(tmp_path):
    """A map kept as JSON reads back as it was given, from a store's files and from a dataset
    directory: nodes keyed by ``EntityId`` (so their methods work on keys read from disk), edges
    by ``RelationId``, also when given as plain text."""
    from nolli.logic.formats.dataset import Dataset, read_dataset, write_dataset
    from nolli.model.keys import Repo
    from nolli.model.model import Model, Relation
    from nolli.model.schema import EntityId, RelationId, RelationKind
    from nolli.store import RepoStoreProvider

    a, b = EntityId.from_path("a.py"), EntityId.from_path("b.py", "B")
    e = Relation(RelationKind.USE, a, b, "import").id
    given = {a: {"x": 1}, a.own_code(): {}, str(e): {"y": 2}}
    key = Repo("github.com/o/r").make_commit("a" * 40)
    store = RepoStoreProvider(tmp_path / "data").open(key.repo)
    store.open_maps("m").save(key.format_key(), given, {"by": "t"})
    write_dataset(Dataset(key, Model(key), {"m": (given, {"by": "t"})}), tmp_path / "ds")
    for values, about in [(store.open_maps("m").load(key.format_key()), store.open_maps("m").load_about(key.format_key())),
                          read_dataset(tmp_path / "ds").maps["m"]]:
        assert values == {a: {"x": 1}, a.own_code(): {}, e: {"y": 2}} and about["by"] == "t"
        assert all(type(k) is (RelationId if k == e else EntityId) for k in values)
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
    preset and side, list its changes, describe every box, brief the agent, and keep its
    verdicts and list them. A rename is what git's per-file content says the most about."""
    from helpers import spec

    from nolli.model.schema import EntityId

    path, base, head = renaming
    change = Repo(str(path)).make_change(base, head)
    _fetch_change(open_store(tmp_path, change.repo), LocalRepository(path), change, about={"title": "rename"})

    store = open_store(tmp_path, change.repo)
    sc = open_scene(store, change)
    renamed = EntityId.from_path("shop/helpers.py")
    assert sc.find_element(renamed).get("change")["old_path"] == "shop/legacy.py"

    for preset in ("folded", "changes", "unfolded", "whole"):
        for side in (None, "base", "head"):
            st = spec(preset=preset, **({"side": side} if side else {}))
            assert viewer.export_scene(_draw(sc, st))["nodes"]
            drawn = _draw(sc, st)
            text, ids = mermaid.render(drawn), mermaid.list_ids(drawn)
            assert text and ids
    unfolded = viewer.export_scene(_draw(sc, spec(expanded=["repo:", "repo:shop"])))
    assert {n["id"] for n in unfolded["nodes"]} >= {"repo:shop/helpers.py", "repo:shop/api.py", "repo:shop/store.py"}

    assert any(r["id"] == "repo:shop/helpers.py" for r in list_changes(sc))

    told = brief.make_brief(sc)
    assert "helpers" in told
    review_ids = [i for i in re.findall(r"\bn_\w+", mermaid.render(brief.project_for_review(sc))) if not i.endswith("_g")]
    verdicts = [{"target": t, "verdict": "good", "reason": "fine"} for t in dict.fromkeys(review_ids)]
    kept, problems = operations.add_comments(store, operations.make_review_key(change), json.dumps(verdicts), "test")
    assert kept and not problems, problems

    again = open_store(tmp_path, change.repo)  # the comments read back from JSON too
    sc2 = open_scene(again, change)
    listed = comments.describe_comments(sc2, sc2.overlays["comments"].list_all(sc2))
    assert len(listed) == len(kept) and all(type(x) is EntityId for c in listed for x in c["ends"])
    assert viewer.export_scene(_draw(sc2, spec(preset="changes")))["nodes"]


def test_a_store_answers_for_its_repository_only(src, tmp_path):
    """A store keeps one repository's models (as a hosted server's does): a change of the store's
    repository projects in every preset, every scene drawn on the way asking only for keys of
    that repository, and a key of another repository, or of none, is refused."""
    from helpers import spec

    from nolli.model.keys import NotAllowed

    path, shas = src
    change = Repo(str(path)).make_change(shas[0], shas[2])
    store = open_store(tmp_path, change.repo)
    _fetch_change(store, LocalRepository(path), change, about={"title": "checked"})
    sc = open_scene(store, change)
    for preset in ("folded", "changes", "unfolded"):
        assert viewer.export_scene(_draw(sc, spec(preset=preset)))["nodes"]
    for key in (StringKey(), Repo("github.com/o/other").make_commit("a" * 40)):
        with pytest.raises(NotAllowed):
            store.list_map_names(key.format_key())
