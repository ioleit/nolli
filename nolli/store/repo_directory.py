"""A ModelStore bound to one repository, kept in directories of JSON files, and the provider
that decides who may open one.

``RepoStoreProvider(root).open(repo)`` decides once whether the current request may read
``repo`` (``allow``, the one method a service overrides) and returns a ``RepoDirectoryStore``
that keeps only that repository's models: a key of another repository raises ``NotAllowed``,
and one of no repository (a transformation's) has nothing kept and cannot be kept. A request that holds the store for one repository cannot read another's by
mistake, and what runs later for it (a build, a review) holds the same store.

Where a model is kept follows from its key alone, so nothing is scanned or indexed:

    <root>/github.com/<owner>/<repo>/<rest of the key>/   owner and repo lower-cased, as GitHub reads them
    <root>/local/<the repository's path>/<rest of the key>/

``<rest of the key>`` is ``format_key()`` after the repository's URL (``@<sha>``,
``@<base>..<head>``, ``@<sha>~project:<spec>``, ``@~<text>``), escaped for any file system
(``_encode``) and cut into names of at most 200 bytes. A model's directory holds
``key.json``, ``model.json`` and ``overlays/<name>.json``, and for a change ``change.json``;
a change's two commits are kept in their own directories beside it. The layout is this
module's own: nothing outside it reads these files (``nolli/datasets.py`` writes the
files the command line exchanges, through the store).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from pathlib import Path

from nolli.model import serialized
from nolli.model.keys import ChangeKey, ModelKey, NotAllowed, Repo
from nolli.model.model import Key, Model
from nolli.model.scene import Scene
from nolli.store import precomputed
from nolli.store.precomputed import OverlayStore

FORMAT = 1  # of key.json and change.json
SEGMENT = 200  # bytes in one name of a key's path, at most (file systems allow 255)
CACHE_ENTRIES = 4096  # parsed files kept by a provider, at most
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._@=,+-")
_MORE = "~"  # ends every name of a key's path but the last; never in an encoded name


# -- paths ----------------------------------------------------------------------------


def _encode(text: str) -> str:
    """Escape ``text`` for a file name on any file system: an upper-case letter as ``^`` and the
    letter (macOS's file system ignores case by default), any other character outside ``_SAFE`` as
    ``%XX`` per UTF-8 byte, and a name of dots only (``.``, ``..``) with its first dot escaped."""
    out = []
    for c in text:
        if c in _SAFE:
            out.append(c)
        elif "A" <= c <= "Z":
            out.append("^" + c.lower())
        else:
            out.extend(f"%{b:02X}" for b in c.encode())
    s = "".join(out)
    return "%2E" + s[1:] if s and set(s) == {"."} else s


def _decode(name: str) -> str:
    """Return the text ``_encode`` made ``name`` from."""
    out, raw, i = [], bytearray(), 0
    while i < len(name):
        c = name[i]
        if c == "%":
            raw.append(int(name[i + 1:i + 3], 16))
            i += 3
            continue
        if raw:
            out.append(raw.decode())
            raw.clear()
        if c == "^":
            out.append(name[i + 1].upper())
            i += 2
            continue
        out.append(c)
        i += 1
    if raw:
        out.append(raw.decode())
    return "".join(out)


def _split(encoded: str) -> list[str]:
    """Cut an encoded name into names of at most ``SEGMENT`` bytes, never inside an escape, each
    but the last ending in ``_MORE``."""
    parts, rest = [], encoded
    while len(rest) > SEGMENT:
        cut = SEGMENT
        while rest[cut - 1] in "%^" or (cut >= 2 and rest[cut - 2] == "%"):  # an escape is not cut
            cut -= 1
        parts.append(rest[:cut] + _MORE)
        rest = rest[cut:]
    parts.append(rest)
    return parts


def _format_repo_path(repo: Repo) -> Path:
    """Return the directory of ``repo``'s models relative to the root: ``github.com/<owner>/<repo>``
    for one on GitHub (lower-cased), else ``local/`` and the repository's path."""
    url = repo.url.rstrip("/")
    if repo.format_remote():
        return Path(*url.lower().split("/"))
    return Path("local", *(_encode(p) for p in url.split("/") if p))


def _format_key_path(key: ModelKey) -> Path:
    """Return the directory of the model with ``key`` relative to its repository's directory."""
    text, prefix = key.format_key(), key.repo.url + "@"
    if not text.startswith(prefix):
        raise ValueError(f"{text} does not start with its repository {key.repo.url}")
    return Path(*_split(_encode(text[len(prefix) - 1:])))


def _parse_key_path(repo: Repo, path: Path) -> ModelKey:
    """Return the key whose directory, relative to ``repo``'s, is ``path`` (``_format_key_path``'s inverse)."""
    rest = "".join(p[:-1] if p.endswith(_MORE) else p for p in path.parts)
    return ModelKey.parse(repo.url + _decode(rest))


# -- the provider ---------------------------------------------------------------------


class ParseCache:
    """Parsed store files, by file and what they were read as, each kept until the file changes
    (its inode or modification time): a file read again is not parsed again. At most
    ``entries`` are kept, the least recently used going first."""

    def __init__(self, entries: int = CACHE_ENTRIES) -> None:
        self.entries = entries
        self.kept: OrderedDict[tuple[Path, str], tuple[tuple[int, int], object]] = OrderedDict()
        self.lock = threading.Lock()

    def read(self, f: Path, parse: Callable[[dict], object], tag: str = ""):
        """Return ``parse(json)`` of the file ``f``, or None if it is missing, unreadable or of another
        format. ``tag`` tells apart two ways of reading one file."""
        try:
            st = f.stat()
        except OSError:
            return None
        version, k = (st.st_ino, st.st_mtime_ns), (f, tag)
        with self.lock:
            hit = self.kept.get(k)
            if hit and hit[0] == version:
                self.kept.move_to_end(k)
                return hit[1]
        try:
            value = parse(json.loads(f.read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            value = None
        with self.lock:
            self.kept[k] = (version, value)
            self.kept.move_to_end(k)
            while len(self.kept) > self.entries:
                self.kept.popitem(last=False)
        return value


class RepoStoreProvider:
    """Opens the stores of the repositories under ``root``, one per repository and request.
    ``allow`` decides who may open which; this one lets everyone open every one."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache = ParseCache()

    def allow(self, repo: Repo) -> None:
        """Raise NotAllowed unless the current request may read ``repo``. A service built on nolli
        overrides this, and nothing else."""

    def open(self, repo: Repo) -> RepoDirectoryStore:
        """Return the store of ``repo``'s models, once the current request may read them (``allow``)."""
        self.allow(repo)
        return RepoDirectoryStore(self.root / _format_repo_path(repo), repo, self.cache)

    def locate(self, key: ModelKey) -> Path:
        """Return the directory the model with ``key`` is kept in, whether it is there or not (for
        retention, which deletes models it does not read)."""
        return self.root / _format_repo_path(key.repo) / _format_key_path(key)


# -- the store ------------------------------------------------------------------------


def _swap(out: Path, write: Callable[[Path], None]) -> None:
    """Write a directory next to ``out`` (``write(tmp)``), then put it in place of ``out`` whole."""
    tag = f"{os.getpid()}.{threading.get_ident()}"
    tmp, old = out.parent / f".{out.name}.{tag}", out.parent / f".{out.name}.old.{tag}"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        write(tmp)
        if out.exists():
            out.rename(old)
        tmp.rename(out)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_json(f: Path, value: dict, indent: int | None = None) -> None:
    """Write ``value`` to ``f`` through a file beside it, so a reader sees the old file or the new one."""
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(value, indent=indent))
    tmp.replace(f)


class RepoDirectoryStore(OverlayStore):
    """The models of one repository, any key of it, with the maps kept for them; in the
    directory ``path`` (``RepoStoreProvider.open`` gives one). Every method refuses a key of
    another repository (``NotAllowed``); a key of no repository reads as nothing kept and cannot
    be written."""

    def __init__(self, path: Path, repo: Repo, cache: ParseCache | None = None) -> None:
        self.path, self.repo, self.cache = Path(path), repo, cache or ParseCache()

    def own(self, key: ModelKey) -> ModelKey:
        """Return ``key`` if it is one of this store's repository, else raise NotAllowed."""
        if getattr(key, "repo", None) != self.repo:
            raise NotAllowed(f"{key.format_key()} is no model of {self.repo.url}")
        return key

    def find(self, key: ModelKey) -> Path | None:
        """Return the directory a read of ``key`` looks in: None for a key of no repository (a
        transformation's, which nothing keeps), and NotAllowed for another repository's."""
        return None if getattr(key, "repo", None) is None else self.locate(key)

    def locate(self, key: ModelKey) -> Path:
        """Return the directory of the model with ``key``, whether it is there or not."""
        return self.path / _format_key_path(self.own(key))

    # -- models and changes

    def load_model(self, key: ModelKey) -> Model | None:
        """Return the model kept under ``key`` (a change's merged model, if one was kept), or None."""
        d = self.find(key)
        return None if d is None else self.cache.read(d / "model.json", lambda j: serialized.parse_model(j, key), key.format_key())

    def save_model(self, key: ModelKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        """Keep a model under ``key`` with the maps its extractors made, replacing what was kept."""
        out = self.locate(key)
        _swap(out, lambda tmp: self._write_model(tmp, key, model, maps))

    def load_change(self, key: ChangeKey) -> dict | None:
        """Return what a change says about itself, or None unless it and both its commits are kept."""
        about = self.cache.read(self.locate(key) / "change.json", lambda j: j["about"] if j.get("format") == FORMAT else None)
        if about is None or any(self.load_model(c) is None for c in key.list_sides()):
            return None
        return about

    def save_change(self, key: ChangeKey, about: dict, sides: Mapping[str, tuple[Model, Mapping[str, Mapping[Key, dict]]]],
                    maps: Mapping[str, Mapping[Key, dict]], model: Model | None = None) -> None:
        """Keep a change: its two commits (each in its own directory), what it says about itself,
        the maps kept for it and its merged model. A review's verdicts kept for it stay unless
        ``maps`` has new ones."""
        for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
            self.save_model(commit, *sides[side])
        out = self.locate(key)

        def write(tmp: Path) -> None:
            if model is not None:
                self._write_model(tmp, key, model, maps)
            else:
                self._write_key(tmp, key)
                self._write_maps(tmp, key, maps)
            _write_json(tmp / "change.json", {"format": FORMAT, "about": about}, indent=1)
            kept = out / "overlays" / "verdicts.json"  # a review stays with the change it answers
            if kept.is_file() and "verdicts" not in maps:
                shutil.copy2(kept, tmp / "overlays" / "verdicts.json")

        _swap(out, write)

    def save_scene(self, scene: Scene, about: dict | None = None) -> None:
        """Keep the scene's model under its key, and what every overlay on it says about it."""
        self.save_model(scene.model.key, scene.model, {})
        for o in scene.overlays.values():
            self.precompute_overlay_values(scene, o, about)

    def _write_key(self, d: Path, key: ModelKey) -> None:
        _write_json(d / "key.json", {"format": FORMAT, **key.to_dict()})

    def _write_model(self, d: Path, key: ModelKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        model.key = key
        self._write_key(d, key)
        _write_json(d / "model.json", serialized.dump_model(model), indent=1)
        self._write_maps(d, key, maps)

    def _write_maps(self, d: Path, key: ModelKey, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        (d / "overlays").mkdir(exist_ok=True)
        for name, values in maps.items():
            _write_json(d / "overlays" / f"{_encode(name)}.json", serialized.dump_map(key.format_key(), values, None))

    # -- maps

    def open_maps(self, name: str) -> RepoDirectoryMaps:
        return RepoDirectoryMaps(self, name)

    def list_map_names(self, model_key: str) -> list[str]:
        """Return the names of the maps kept for the model with key text ``model_key``."""
        d = self.find(ModelKey.parse(model_key))
        return sorted(_decode(f.stem) for f in (d / "overlays").glob("*.json")) if d is not None and (d / "overlays").is_dir() else []

    def locate_maps(self, model_key: str, name: str, *, writing: bool = False) -> Path | None:
        """Return the file of the map ``name`` kept for the model with key text ``model_key``: for a
        read, None for a key of no repository (``find``); a write needs one of this repository."""
        key = ModelKey.parse(model_key)
        d = self.locate(key) if writing else self.find(key)
        return None if d is None else d / "overlays" / f"{_encode(name)}.json"


class RepoDirectoryMaps:
    """A ``RepoDirectoryStore``'s maps of one name. A map can be kept for any key of the store's
    repository, whether a model is kept under it or not (a projection's)."""

    def __init__(self, store: RepoDirectoryStore, name: str) -> None:
        self.store, self.name = store, name

    def read(self, model_key: str) -> tuple[dict[Key, dict], dict | None] | None:
        f = self.store.locate_maps(model_key, self.name)
        return None if f is None else self.store.cache.read(f, serialized.parse_map)

    def load(self, model_key: str) -> Mapping[Key, dict]:
        got = self.read(model_key)
        return got[0] if got else {}

    def load_about(self, model_key: str) -> dict | None:
        got = self.read(model_key)
        return got[1] if got else None

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None:
        _write_json(self.store.locate_maps(model_key, self.name, writing=True),  # type: ignore[arg-type]  # never None for a write
                    serialized.dump_map(model_key, values, precomputed.stamp(about)), indent=1)
