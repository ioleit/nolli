"""A storage that keeps models and changes in directories with JSON files, and how a model is written as one (`model.json`)."""
from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Mapping
from pathlib import Path

from nolli.model import keys
from nolli.model.keys import ChangeKey, CommitKey, ModelKey, Repo, StringKey
from nolli.model.model import Entity, Key, Model, Relation
from nolli.model.scene import Scene
from nolli.store import maps as maps_lib
from nolli.store.precomputed import OverlayStore

FORMAT = 1  # of key.json and change.json (nolli.store.FORMAT)
MODEL_FORMAT = 3  # of model.json; a model of another format is built again, never read (3: its maps are by shape, meta names no overlays)


# -- models in directories ------------------------------------------------------------


def _format_dir_name(key: ModelKey) -> str:
    """The directory a model is kept in: ``<owner>_<repo>-<sha7>`` for a commit,
    ``<owner>_<repo>-<base7>-<head7>`` for a change (the directory's name for a repository
    not on GitHub)."""
    if isinstance(key, ChangeKey):
        return f"{key.repo.format_slug()}-{key.base[:7]}-{key.head[:7]}"
    if isinstance(key, CommitKey):
        return f"{key.repo.format_slug()}-{key.sha[:7]}"
    return key.format_key()


class DirectoryStore(OverlayStore):
    """Models as directories under ``root``, one per key, named after it:

    - a commit: ``key.json``, ``model.json`` and ``overlays/<name>.json``, one per overlay
      with something kept for the model (its extractors');
    - a change: ``key.json``, ``change.json`` (what it says about itself), ``base/`` and
      ``head/`` (its commits, as above) and ``overlays/`` (git's hunks, verdicts).

    A directory written elsewhere (``nolli diff -o``) is read the same way.
    Every read goes through ``check`` (no-op here: a service built on nolli overrides it).
    """

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.index: dict[ModelKey, Path] = {}  # key -> its directory
        self.names: dict[str, ModelKey] = {}  # directory name -> key
        self.places: dict[str, Path] = {}  # model key (text) -> directory, a change's sides included
        self.scanned = 0
        self.cache: dict[Path, tuple[tuple[int, int], object]] = {}  # file -> (inode, mtime), what it parsed to

    def check(self, repo: Repo) -> None:
        """Raises NotAllowed unless the current request may read ``repo``: everyone may, here."""

    def locate(self, key: ModelKey) -> Path:
        """The model's directory (existing or not), for code that works with its files."""
        self.scan()
        return self.index.get(key) or self.root / _format_dir_name(key)

    def scan(self) -> None:
        try:
            mtime = self.root.stat().st_mtime_ns
        except OSError:
            return
        with self.lock:
            if mtime == self.scanned:
                return
            self.scanned = mtime
            index, names, places = {}, {}, {}
            for d in sorted(self.root.iterdir()):
                key = _read_key(d) if not d.name.startswith(".") else None
                if key is None:
                    continue
                index.setdefault(key, d)
                names[d.name] = key
                places.setdefault(key.format_key(), d)
                if isinstance(key, ChangeKey):
                    for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
                        places.setdefault(commit.format_key(), d / side)
            for k, d in index.items():  # a commit's own directory before a change's side
                places[k.format_key()] = d
            self.index, self.names, self.places = index, names, places

    def find_key(self, name: str) -> ModelKey | None:
        """The key of the model in the directory ``name`` under the root, None if there is none."""
        if not name or "/" in name or name.startswith("."):
            return None
        self.scan()
        if name not in self.names and (self.root / name / "key.json").is_file():
            self.scanned = 0  # written since: read again
            self.scan()
        return self.names.get(name)

    def read_json(self, f: Path, parse):
        """``parse(json)`` of a file, kept until the file changes; None if missing, unreadable
        or of another format."""
        try:
            st = f.stat()
        except OSError:
            return None
        version = (st.st_ino, st.st_mtime_ns)
        hit = self.cache.get(f)
        if hit and hit[0] == version:
            return hit[1]
        try:
            value = parse(json.loads(f.read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            value = None
        self.cache[f] = (version, value)
        return value

    def load_model(self, key: ModelKey) -> Model | None:
        """A commit's model; a change's merged model, if it was kept with the change."""
        self.check(key.repo)
        self.scan()
        d = self.places.get(key.format_key())
        if d is None:
            return None
        m = self.read_json(d / "model.json", _parse_model)
        if m is not None:
            m.key = key
        return m

    def load_change(self, key: ChangeKey) -> dict | None:
        self.check(key.repo)
        d = self.locate(key)
        about = self.read_json(d / "change.json", lambda j: j["about"] if j.get("format") == FORMAT else None)
        if about is None:
            return None
        sides = all(self.load_model(c) is not None for c in key.list_sides())
        return about if sides else None

    def save_model(self, key: CommitKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        out = self.locate(key)
        self.swap(out, lambda tmp: write_commit(tmp, key, model, maps))
        with self.lock:
            self.index[key], self.names[out.name], self.places[key.format_key()] = out, key, out

    def save_change(self, key: ChangeKey, about: dict, sides, maps, model: Model | None = None) -> None:
        out = self.locate(key)

        def write(tmp: Path) -> None:
            write_change(tmp, key, about, sides, maps, model)
            kept = out / "overlays" / "verdicts.json"  # a review stays with the change it answers
            if kept.is_file() and "verdicts" not in maps:
                shutil.copy2(kept, tmp / "overlays" / "verdicts.json")

        self.swap(out, write)
        with self.lock:
            self.index[key], self.names[out.name], self.places[key.format_key()] = out, key, out
            for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
                self.places.setdefault(commit.format_key(), out / side)

    def swap(self, out: Path, write) -> None:
        """Written next to the final directory, then swapped in whole."""
        tag = f"{os.getpid()}.{threading.get_ident()}"
        tmp, old = self.root / f".{out.name}.{tag}", self.root / f".{out.name}.old.{tag}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            write(tmp)
            if out.exists():
                out.rename(old)
            tmp.rename(out)
            shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- maps ------------------------------------------------------------------

    def open_maps(self, name: str) -> DirectoryMaps:
        return DirectoryMaps(self, name)

    def list_map_names(self, model_key: str) -> list[str]:
        """The maps kept for a model: the files under its ``overlays/``."""
        if not _is_kept_here(model_key):
            return []
        self.check(Repo(model_key.rpartition("@")[0]))
        self.scan()
        d = self.places.get(model_key)
        return sorted(f.stem for f in (d / "overlays").glob("*.json")) if d is not None and (d / "overlays").is_dir() else []

    def save_scene(self, scene: Scene, by: dict | None = None) -> None:
        """Not yet: this store keeps models under commit and change keys only (``save_model``,
        ``save_change``). What an overlay says about one it holds is kept with
        ``precompute_overlay_values``."""
        raise NotImplementedError("DirectoryStore keeps models by commit or change key: save_model, save_change")

    def locate_maps(self, model_key: str, name: str) -> Path | None:
        """Where a model's map is; checked, as every read (the repository is the key's text before ``@``)."""
        if not _is_kept_here(model_key):
            return None
        self.check(Repo(model_key.rpartition("@")[0]))
        self.scan()
        d = self.places.get(model_key)
        return None if d is None else d / "overlays" / f"{name}.json"


def _is_kept_here(model_key: str) -> bool:
    """Whether this store can hold maps for ``model_key``: a commit's or a change's
    (``<repo>@…``). For any other key (the ``StringKey`` of the scene a projection returns)
    nothing is kept here, so nothing is read: the answer is "none kept", without a lookup."""
    return "@" in model_key


class DirectoryMaps:
    def __init__(self, store: DirectoryStore, name: str) -> None:
        self.store, self.name = store, name

    def read(self, model_key: str) -> tuple[dict[Key, dict], dict | None] | None:
        f = self.store.locate_maps(model_key, self.name)
        return None if f is None else self.store.read_json(f, maps_lib.parse_map)

    def load(self, model_key: str) -> Mapping[Key, dict]:
        got = self.read(model_key)
        return got[0] if got else {}

    def load_about(self, model_key: str) -> dict | None:
        got = self.read(model_key)
        return got[1] if got else None

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None:
        f = self.store.locate_maps(model_key, self.name)
        if f is None:
            raise KeyError(f"no model {model_key} to keep {self.name} for")
        f.parent.mkdir(exist_ok=True)
        tmp = f.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(maps_lib.dump_map(model_key, values, about), indent=1))
        tmp.replace(f)


# -- model.json -----------------------------------------------------------------------


def _dump_model(model: Model) -> dict:
    """A model's ``model.json`` form: its structure. What a model made from others stands for
    there is its scene's, said again when the scene is made (``merge.union`` for a kept change)."""
    return {"format": MODEL_FORMAT, "key": model.key.format_key(), "meta": model.meta,
            "nodes": {x: model.entities[x].kind for x in sorted(model.entities)},
            "edges": [[e.kind, e.src, e.dst, e.subkind] for e in model.edges]}


def _parse_model(d: dict) -> Model:
    """A model from its ``model.json`` form, which carries its ``format``; another format
    raises ``FormatError`` rather than be misread. Its key is a ``StringKey`` of the text kept:
    the store sets the key it read the model by."""
    if d.get("format") != MODEL_FORMAT:
        raise maps_lib.FormatError(f"model.json of format {d.get('format')!r}, this nolli reads {MODEL_FORMAT}")
    return Model(StringKey(d.get("key", "")), (Entity(x, k) for x, k in d["nodes"].items()), (Relation(*e) for e in d["edges"]),
                 meta=d.get("meta") or {})  # a ``refs`` kept by an older nolli is ignored


def _write_model(f: Path, model: Model) -> None:
    f.write_text(json.dumps(_dump_model(model), indent=1))


def _write_maps(d: Path, model_key: str, maps: Mapping[str, Mapping[Key, dict]]) -> None:
    (d / "overlays").mkdir(exist_ok=True)
    for name, values in maps.items():
        (d / "overlays" / f"{name}.json").write_text(json.dumps(maps_lib.dump_map(model_key, values, None)))


def write_commit(d: Path, key: CommitKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
    model.key = key
    (d / "key.json").write_text(json.dumps({"format": FORMAT, **key.to_dict()}))
    _write_model(d / "model.json", model)
    _write_maps(d, key.format_key(), maps)


def write_change(d: Path, key: ChangeKey, about: dict, sides, maps: Mapping[str, Mapping[Key, dict]], model: Model | None = None) -> None:
    """A change's directory (``DirectoryStore``) at ``d``, which exists; ``model``: the change's
    merged model (``nolli/logic/transformations/merge.py``), kept so it is not merged again on every read."""
    (d / "key.json").write_text(json.dumps({"format": FORMAT, **key.to_dict()}))
    (d / "change.json").write_text(json.dumps({"format": FORMAT, "about": about}, indent=1))
    if model is not None:
        model.key = key
        _write_model(d / "model.json", model)
    for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
        (d / side).mkdir(exist_ok=True)
        write_commit(d / side, commit, *sides[side])
    _write_maps(d, key.format_key(), maps)


def _read_key(d: Path) -> ModelKey | None:
    """The key of a model directory (its key.json), None for anything else (another format
    included)."""
    try:
        j = json.loads((d / "key.json").read_text())
        return keys.ModelKey.from_dict(j) if j.get("format") == FORMAT else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
