"""A storage that keeps models and changes in directories with JSON files."""
from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Mapping
from pathlib import Path

from nolli import keys
from nolli.keys import ChangeKey, Commit, ModelKey, Repo
from nolli.model import Key, Model
from nolli.store import maps as maps_lib

FORMAT = 1  # of key.json and change.json (nolli.store.FORMAT)


# -- nolli's own: models in directories ----------------------------------------------


class DirectoryStore:
    """Models as directories under ``root``, one per key, named after it:

    - a commit: ``key.json``, ``model.json`` and ``overlays/<name>.json``, one per overlay
      with something kept for the model (its extractors');
    - a change: ``key.json``, ``change.json`` (what it says about itself), ``base/`` and
      ``head/`` (its commits, as above) and ``overlays/`` (git's hunks, verdicts).

    A directory written elsewhere (``nolli diff -o``, a CI run's artifact) is read the same way.
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
        return self.index.get(key) or self.root / key.format_name()

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
                key = read_key(d) if not d.name.startswith(".") else None
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
        if not name or "/" in name or name.startswith("."):
            return None
        self.scan()
        if name not in self.names and (self.root / name / "key.json").is_file():
            self.scanned = 0  # written since: read again
            self.scan()
        return self.names.get(name)

    def list_names(self) -> list[str]:
        self.scan()

        def mtime(name: str) -> float:
            try:
                return (self.root / name / "key.json").stat().st_mtime
            except OSError:
                return 0.0

        return sorted(self.names, key=lambda n: -mtime(n))

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

    def load_model(self, key: Commit) -> Model | None:
        self.check(key.repo)
        self.scan()
        d = self.places.get(key.format_key())
        if d is None:
            return None
        m = self.read_json(d / "model.json", Model.from_dict)
        if m is not None:
            m.key = key.format_key()
        return m

    def load_change(self, key: ChangeKey) -> dict | None:
        self.check(key.repo)
        d = self.locate(key)
        about = self.read_json(d / "change.json", lambda j: j["about"] if j.get("format") == FORMAT else None)
        if about is None:
            return None
        sides = all(self.load_model(c) is not None for c in key.list_sides())
        return about if sides else None

    def save_model(self, key: Commit, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        out = self.locate(key)
        self.swap(out, lambda tmp: write_commit(tmp, key, model, maps))
        with self.lock:
            self.index[key], self.names[out.name], self.places[key.format_key()] = out, key, out

    def save_change(self, key: ChangeKey, about: dict, sides, maps) -> None:
        out = self.locate(key)

        def write(tmp: Path) -> None:
            write_change(tmp, key, about, sides, maps)
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

    def locate_maps(self, model_key: str, name: str) -> Path | None:
        """Where a model's map is; checked, as every read (the repository is the key's text before ``@``)."""
        self.check(Repo(model_key.rpartition("@")[0]))
        self.scan()
        d = self.places.get(model_key)
        return None if d is None else d / "overlays" / f"{name}.json"


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


def write_maps(d: Path, model_key: str, maps: Mapping[str, Mapping[Key, dict]]) -> None:
    (d / "overlays").mkdir(exist_ok=True)
    for name, values in maps.items():
        (d / "overlays" / f"{name}.json").write_text(json.dumps(maps_lib.dump_map(model_key, values, None)))


def write_commit(d: Path, key: Commit, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
    model.key = key.format_key()
    (d / "key.json").write_text(json.dumps({"format": FORMAT, **keys.dump_key(key)}))
    model.save(d / "model.json")
    write_maps(d, model.key, maps)


def write_change(d: Path, key: ChangeKey, about: dict, sides, maps: Mapping[str, Mapping[Key, dict]]) -> None:
    """A change's directory (``DirectoryStore``) at ``d``, which exists."""
    (d / "key.json").write_text(json.dumps({"format": FORMAT, **keys.dump_key(key)}))
    (d / "change.json").write_text(json.dumps({"format": FORMAT, "about": about}, indent=1))
    for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
        (d / side).mkdir(exist_ok=True)
        write_commit(d / side, commit, *sides[side])
    write_maps(d, key.format_key(), maps)


def read_key(d: Path) -> ModelKey | None:
    """The key of a model directory (its key.json), None for anything else (another format
    included)."""
    try:
        j = json.loads((d / "key.json").read_text())
        return keys.parse_key(j) if j.get("format") == FORMAT else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
