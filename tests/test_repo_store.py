"""nolli's own store (nolli/store/repo_directory.py): a store per repository, opened once per
request by its provider, keeping any key of that repository where the key says; and dataset
directories (nolli/datasets.py), the files that go in and out of a store."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nolli import datasets, operations
from nolli.logic.sources import LocalRepository
from nolli.model.keys import ModelKey, NotAllowed, ProjectionKey, Repo, StringKey
from nolli.model.model import Model
from nolli.model.scene import FixedOverlay
from nolli.operations import open_scene
from nolli.server import serve
from nolli.store import MemoryStore, RepoStoreProvider, repo_directory
from nolli.store.repo_directory import _format_key_path, _format_repo_path, _parse_key_path

REPO = Repo("github.com/O/R")
C1, C2 = REPO.make_commit("1" * 40), REPO.make_commit("2" * 40)
CHANGE = C1.make_change_to(C2.sha)


def model(name: str) -> Model:
    return Model(meta={"repo": name})


def test_a_key_is_kept_where_its_text_says():
    """Any key of a repository has a directory of its own, worked out from the key alone and
    back: case kept on a file system that ignores it, no name over 255 bytes."""
    keys = [C1, CHANGE, ProjectionKey(CHANGE, "p=default"), ProjectionKey(CHANGE, "x=repo:Shop/A.py,repo:shop/a.py" * 20),
            StringKey("files@worktree", REPO), ProjectionKey(StringKey("picked", REPO), "p=folded")]
    paths = [_format_key_path(k) for k in keys]
    for k, p in zip(keys, paths, strict=True):
        assert _parse_key_path(REPO, p) == k
        assert all(len(part.encode()) <= 255 for part in p.parts)
    assert len({str(p).lower() for p in paths}) == len(paths)  # apart on a file system that ignores case
    assert str(_format_key_path(C1)) == "@" + "1" * 40 and str(_format_key_path(CHANGE)) == f"@{'1' * 40}..{'2' * 40}"
    assert _format_repo_path(REPO) == Path("github.com/o/r")
    assert _format_repo_path(Repo("/home/me/shop")) != _format_repo_path(Repo("/srv/shop"))  # two checkouts called shop


def test_string_keys_name_their_repository():
    k = StringKey("files@worktree", REPO)
    assert k.format_key() == "github.com/O/R@~files@worktree"
    assert ModelKey.parse(k.format_key()) == k and ModelKey.from_dict(k.to_dict()) == k
    assert ModelKey.parse("picked") == StringKey("picked") and StringKey("picked").repo is None
    assert ProjectionKey(k, "p=default").repo == REPO and ModelKey.parse(ProjectionKey(k, "p=default").format_key()).source == k


def test_a_store_keeps_models_changes_and_verdicts(tmp_path):
    store = RepoStoreProvider(tmp_path / "data").open(REPO)
    assert store.load_change(CHANGE) is None and store.load_model(C1) is None
    store.save_model(C1, model("one"), {"fs": {"repo:": {"name": "one"}}})
    assert store.load_model(C1).meta["repo"] == "one" and store.load_model(C1).key == C1
    assert store.open_maps("fs").load(C1.format_key()) == {"repo:": {"name": "one"}}
    sides = {"base": (model("base"), {}), "head": (model("head"), {"fs": {"repo:": {"name": "head"}}})}
    store.save_change(CHANGE, {"title": "a change"}, sides, {"content": {"repo:a.py": {"status": "added"}}})
    assert store.load_change(CHANGE) == {"title": "a change"}
    assert store.load_model(C2).meta["repo"] == "head" and store.load_model(C1).meta["repo"] == "base"  # its commits, kept as commits
    assert store.open_maps("fs").load(C2.format_key()) == {"repo:": {"name": "head"}}
    assert store.open_maps("content").load(CHANGE.format_key()) == {"repo:a.py": {"status": "added"}}
    assert sorted(f.name for f in store.locate(CHANGE).iterdir()) == ["change.json", "key.json", "overlays"]  # no copies of its sides

    verdicts = store.open_maps("verdicts")
    assert verdicts.load(CHANGE.format_key()) == {} and verdicts.load_about(CHANGE.format_key()) is None
    kept = {"repo:x": {"verdicts": [{"id": "V1", "target": "n_x", "verdict": "good", "reason": "ok"}]}}
    verdicts.save(CHANGE.format_key(), kept, {"by": "upload"})
    store.save_change(CHANGE, {"title": "rebuilt"}, sides, {"content": {}})  # the verdicts stay with the change they answer
    about = verdicts.load_about(CHANGE.format_key())
    assert verdicts.load(CHANGE.format_key()) == kept and about == {"by": "upload", "at": about["at"]}  # save says when
    assert store.load_change(CHANGE) == {"title": "rebuilt"}
    assert not [p for p in store.path.rglob(".*")]  # no work left over

    projected = ProjectionKey(CHANGE, "p=default")  # a map for a key no model is kept under
    store.open_maps("layout").save(projected.format_key(), {"repo:": {"x": 1}})
    assert store.open_maps("layout").load(projected.format_key()) == {"repo:": {"x": 1}}
    assert store.list_map_names(projected.format_key()) == ["layout"] and store.load_model(projected) is None

    import shutil
    shutil.rmtree(store.locate(C1))  # a change without one of its commits is not kept
    assert store.load_change(CHANGE) is None


def test_a_model_is_stored_whole_or_not_at_all(tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError("half way")

    store = RepoStoreProvider(tmp_path / "data").open(REPO)
    store.save_model(C1, model("before"), {})
    monkeypatch.setattr(repo_directory.RepoDirectoryStore, "_write_model", broken)
    with pytest.raises(RuntimeError):
        store.save_model(C1, model("x"), {})
    assert list(store.path.iterdir()) == [store.locate(C1)]  # the model kept before, no work left over
    assert store.load_model(C1).meta["repo"] == "before"


def test_a_store_keeps_its_repository_only(tmp_path):
    """Another repository's key is refused, to read or to write; a key of no repository (a
    transformation's) has nothing kept, and cannot be kept."""
    store = RepoStoreProvider(tmp_path / "data").open(REPO)
    other = Repo("github.com/o/other").make_commit("a" * 40)
    for read in (lambda: store.load_model(other), lambda: store.load_change(other.make_change_to("b" * 40)),
                 lambda: store.open_maps("fs").load(other.format_key()), lambda: store.list_map_names(other.format_key())):
        with pytest.raises(NotAllowed):
            read()
    with pytest.raises(NotAllowed):
        store.save_model(other, model("x"), {})
    with pytest.raises(NotAllowed):
        store.open_maps("fs").save(other.format_key(), {})
    nobody = StringKey("picked")
    assert store.load_model(nobody) is None and store.open_maps("fs").load(nobody.format_key()) == {}
    assert store.list_map_names(nobody.format_key()) == [] and store.locate_maps("", "change") is None
    with pytest.raises(NotAllowed):
        store.save_model(nobody, model("x"), {})
    assert not (tmp_path / "data" / "github.com" / "o" / "other").exists()


def test_a_scene_is_saved_and_opened_whole(tmp_path):
    key = StringKey("fixtures", REPO)
    built = MemoryStore()
    scene = open_scene(built, operations.build_files(built, Path(__file__).parent / "fixtures", key))
    store = RepoStoreProvider(tmp_path / "data").open(REPO)
    store.save_scene(scene.with_overlays(FixedOverlay("mine", {"repo:": {"n": 1}})), {"by": "test"})
    assert store.load_model(key).entities.keys() == scene.model.entities.keys()
    assert {"mine", "files"} <= set(store.list_map_names(key.format_key()))
    opened = store.open_scene(key)
    assert opened.find_element("repo:").get("mine") == {"n": 1}
    assert opened.read_all("files") == scene.read_all("files") and opened.read_all("files")


def test_the_provider_decides_once_per_store(tmp_path):
    asked = []

    class Mine(RepoStoreProvider):
        def allow(self, repo):
            asked.append(repo)
            if repo != REPO:
                raise NotAllowed(repo.url)

    stores = Mine(tmp_path / "data")
    store = stores.open(REPO)
    store.save_model(C1, model("one"), {})
    store.load_model(C1), store.open_maps("fs").load(C1.format_key())
    assert asked == [REPO]
    with pytest.raises(NotAllowed):
        stores.open(Repo("github.com/o/other"))
    assert stores.open(REPO).load_model(C1).meta["repo"] == "one"  # a store opened again reads what was kept


def test_a_refused_repository_is_not_found(tmp_path):
    """The server opens a model's store through its provider: a repository the provider refuses
    answers 404, as one that does not exist."""
    class Refusing(RepoStoreProvider):
        refuse = False

        def allow(self, repo):
            if self.refuse:
                raise NotAllowed(repo.url)

    stores = Refusing(tmp_path / "data")
    stores.open(REPO).save_model(C1, model("one"), {})
    srv = serve.make_server(stores, LocalRepository(tmp_path), tmp_path, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/scene?k={C1.format_key()}"

    def status() -> int:
        try:
            with urllib.request.urlopen(url, timeout=10) as res:
                return res.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        assert status() == 200
        stores.refuse = True
        assert status() == 404
    finally:
        srv.shutdown()
        srv.server_close()


# -- dataset directories ------------------------------------------------------------------


def test_a_dataset_goes_out_and_back_in_whole(tmp_path):
    """A change exported from one store and imported into another (of another kind) opens the
    same, with its maps and where they came from (a review's verdicts)."""
    store = RepoStoreProvider(tmp_path / "data").open(REPO)
    sides = {"base": (model("base"), {"fs": {"repo:": {"name": "base"}}}), "head": (model("head"), {})}
    store.save_change(CHANGE, {"title": "a change"}, sides, {"content": {"repo:a.py": {"status": "added"}}}, model("merged"))
    kept = {"repo:x": {"verdicts": [{"id": "V1", "target": "n_x", "verdict": "good", "reason": "ok"}]}}
    store.open_maps("verdicts").save(CHANGE.format_key(), kept, {"by": "agent", "at": "2026-11-12T00:00:00Z"})

    out = tmp_path / "out"
    out.mkdir()
    (out / "pr.md").write_text("mine")
    datasets.export_dataset(store, CHANGE, out)
    assert sorted(f.name for f in out.iterdir()) == ["base", "change.json", "head", "key.json", "model.json", "overlays", "pr.md"]
    assert datasets.is_dataset(out) and datasets.read_key(out) == CHANGE

    into = MemoryStore()
    assert datasets.import_dataset(into, out) == CHANGE
    assert into.load_change(CHANGE) == {"title": "a change"} and into.load_model(CHANGE).meta["repo"] == "merged"
    assert into.load_model(C1).meta["repo"] == "base" and into.open_maps("fs").load(C1.format_key()) == {"repo:": {"name": "base"}}
    assert into.open_maps("content").load(CHANGE.format_key()) == {"repo:a.py": {"status": "added"}}
    assert into.open_maps("verdicts").load(CHANGE.format_key()) == kept
    assert into.open_maps("verdicts").load_about(CHANGE.format_key()) == {"by": "agent", "at": "2026-11-12T00:00:00Z"}

    again = RepoStoreProvider(tmp_path / "again").open(REPO)  # and into a store on disk, twice
    datasets.import_dataset(again, out)
    datasets.import_dataset(again, out)
    assert again.load_change(CHANGE) == {"title": "a change"} and again.open_maps("verdicts").load(CHANGE.format_key()) == kept
    assert open_scene(again, CHANGE) is not None


def test_a_dataset_of_another_format_is_refused(tmp_path):
    store = RepoStoreProvider(tmp_path / "data").open(REPO)
    store.save_model(C1, model("one"), {})
    out = tmp_path / "out"
    datasets.export_dataset(store, C1, out)
    (out / "key.json").write_text(json.dumps({**json.loads((out / "key.json").read_text()), "format": 0}))
    with pytest.raises(datasets.DatasetError, match="another nolli version"):
        datasets.import_dataset(MemoryStore(), out)
    with pytest.raises(KeyError):
        datasets.export_dataset(store, C2, tmp_path / "none")
