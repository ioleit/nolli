"""Dataset directories: one model as files, the form the command line reads and writes.

A dataset directory is nolli's exchange format, versioned and kept stable: ``nolli build -o
DIR`` writes one, ``nolli view DIR`` and ``nolli agent check DIR`` read one, and tests keep
them as fixtures. It is no store's layout. A store keeps models in a layout of its own, perhaps
not in files at all, and the command line moves a model between the two (``cli.export_dataset``,
``cli.import_dataset``). This module only turns files into a ``Dataset`` and back.

A dataset directory holds:

    key.json  model.json  overlays/<name>.json        a commit's model and its maps
    change.json  base/  head/                         for a change: what it says about itself, and its
                                                      two commits, each as above
    comments.json                                     what was said about the model, if anything
                                                      (nolli/logic/comments.py)
    report.json [changes.json review.mmd review.md]   made from the model, for readers without
                                                      nolli (``cli.write_extras``); never read back

Each map keeps its ``about`` (who made it, when). The forms here are frozen: a store changes its own files freely, and this module changes
only with ``FORMAT``.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from nolli.model import schema
from nolli.model.keys import ChangeKey, ModelKey, StringKey
from nolli.model.model import Entity, Key, Model, Relation
from nolli.model.schema import EntityId, RelationId

FORMAT = 1  # of key.json, change.json and comments.json
MODEL_FORMAT = 3  # of model.json
MAP_FORMAT = 2  # of overlays/<name>.json
DATA = ("key.json", "model.json", "overlays", "change.json", "comments.json", "base", "head")  # the model (write_dataset)
EXTRAS = ("report.json", "changes.json", "review.mmd", "review.md")  # what is made from it

Maps = dict[str, tuple[dict[Key, dict], dict | None]]  # map name -> its values and ``about``


class DatasetError(ValueError):
    """A directory that is no dataset this version of nolli reads."""


@dataclass
class Dataset:
    """One model as a dataset directory holds it: its key, its structure, its maps with their
    ``about``, and for a change what it says about itself and its two commits. A change's
    merged model is optional; a commit's model is not."""

    key: ModelKey
    model: Model | None
    maps: Maps = field(default_factory=dict)
    change: dict | None = None  # a change's ``change.json``
    sides: dict[str, Dataset] = field(default_factory=dict)  # a change's "base" and "head"
    comments: list[dict] = field(default_factory=list)  # ``comments.json``


def remove(d: Path, names: tuple[str, ...]) -> None:
    """Remove the files and directories ``names`` from ``d``, where they exist."""
    for name in names:
        p = Path(d) / name
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)


def is_dataset(path: Path) -> bool:
    """Return whether ``path`` is a dataset directory: one with a ``key.json``."""
    return (Path(path) / "key.json").is_file()


# -- the JSON forms -------------------------------------------------------------------------


def _dump_model(model: Model) -> dict:
    """Return a model's structure as ``model.json`` holds it."""
    return {"format": MODEL_FORMAT, "key": model.key.format_key(), "meta": model.meta,
            "nodes": {x: model.entities[x].kind for x in sorted(model.entities)},
            "edges": [[e.kind, e.src, e.dst, e.subkind] for e in model.edges]}


def _parse_model(d: dict, key: ModelKey | None = None) -> Model:
    """Parse a model from its ``model.json`` form, under ``key`` (by default a ``StringKey`` of the
    text written). Raise DatasetError for another format."""
    if d.get("format") != MODEL_FORMAT:
        raise DatasetError(f"model.json of format {d.get('format')!r}, this nolli reads {MODEL_FORMAT}")
    return Model(key or StringKey(d.get("key", "")), (Entity(x, k) for x, k in d["nodes"].items()),
                 (Relation(*e) for e in d["edges"]), meta=d.get("meta") or {})


def _dump_map(model_key: str, values: Mapping[Key, dict], about: dict | None) -> dict:
    """Return a map as ``overlays/<name>.json`` holds it: its values split into nodes and edges,
    with the model's key and ``about``."""
    keys = {k: schema.parse_key(k) for k in values}  # ids given as plain text (read from JSON) are told apart by their text
    nodes = {k: v for k, v in values.items() if not isinstance(keys[k], RelationId)}
    edges = {k: v for k, v in values.items() if isinstance(keys[k], RelationId)}
    return {"format": MAP_FORMAT, "model": model_key, "about": about, "nodes": nodes, "edges": edges}


def _parse_map(d: dict) -> tuple[dict[Key, dict], dict | None]:
    """Parse a map as ``_dump_map`` wrote it, returning its values (a node keyed by its
    ``EntityId``, an edge by its ``RelationId``) and its ``about``. Raise DatasetError for
    another format."""
    if d.get("format") != MAP_FORMAT:
        raise DatasetError(f"a map of format {d.get('format')!r}, this nolli reads {MAP_FORMAT}")
    values: dict[Key, dict] = {EntityId(k): v for k, v in (d.get("nodes") or {}).items()}
    values.update({RelationId(k): v for k, v in (d.get("edges") or {}).items()})
    return values, d.get("about")


# -- writing --------------------------------------------------------------------------------


def _write(d: Path, ds: Dataset) -> None:
    d.mkdir(parents=True, exist_ok=True)
    text = ds.key.format_key()
    (d / "key.json").write_text(json.dumps({"format": FORMAT, **ds.key.to_dict()}))
    if ds.model is not None:
        (d / "model.json").write_text(json.dumps(_dump_model(ds.model), indent=1))
    (d / "overlays").mkdir(exist_ok=True)
    for name, (values, about) in ds.maps.items():
        (d / "overlays" / f"{name}.json").write_text(json.dumps(_dump_map(text, values, about)))
    if ds.change is not None:
        (d / "change.json").write_text(json.dumps({"format": FORMAT, "about": ds.change}, indent=1))
    if ds.comments:
        (d / "comments.json").write_text(json.dumps({"format": FORMAT, "comments": ds.comments}, indent=1))
    for side, sub in ds.sides.items():
        _write(d / side, sub)


def write_dataset(ds: Dataset, out: Path) -> None:
    """Write ``ds`` to the dataset directory ``out``. A model written there before goes; the
    directory's other files (the extras, a ``pr.md``) stay. Raise DatasetError, deleting nothing,
    for a directory that is no dataset but has files of the names a dataset uses (``base/``,
    ``overlays/``, …): they are someone else's."""
    out = Path(out)
    if is_dataset(out):
        remove(out, DATA)
    elif taken := [n for n in (*DATA, *EXTRAS) if (out / n).exists()]:
        raise DatasetError(f"{out} is no dataset directory, and it has {', '.join(taken)}: name an empty or new directory")
    _write(out, ds)


# -- reading --------------------------------------------------------------------------------


def _read_json(f: Path) -> dict:
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError) as exc:
        raise DatasetError(f"{f}: {exc}") from None


def read_key(d: Path) -> ModelKey:
    """Return the key of the dataset directory ``d``. Raise DatasetError for one of another format."""
    j = _read_json(Path(d) / "key.json")
    if j.get("format") != FORMAT:
        raise DatasetError(f"{d} is a dataset of another nolli version; build it again")
    return ModelKey.from_dict(j)


def _read(d: Path, key: ModelKey, model_needed: bool) -> tuple[Model | None, Maps]:
    f = d / "model.json"
    try:
        model = _parse_model(_read_json(f), key) if f.is_file() or model_needed else None
        maps = {m.stem: _parse_map(_read_json(m)) for m in sorted((d / "overlays").glob("*.json"))}
    except DatasetError as exc:
        raise DatasetError(f"{d} is a dataset of another nolli version ({exc}); build it again") from None
    except (KeyError, TypeError) as exc:
        raise DatasetError(f"{d} is no dataset this nolli reads ({exc}); build it again") from None
    return model, maps


def _read_comments(d: Path) -> list[dict]:
    f = d / "comments.json"
    if not f.is_file():
        return []
    j = _read_json(f)
    if j.get("format") != FORMAT or not isinstance(j.get("comments"), list):
        raise DatasetError(f"{f} is of another nolli version; build it again")
    return j["comments"]


def read_dataset(d: Path) -> Dataset:
    """Read the dataset directory ``d``. Raise DatasetError for a directory that is no dataset of
    this version, or a change whose ``base/`` or ``head/`` is not its commit."""
    d = Path(d)
    key = read_key(d)
    if not isinstance(key, ChangeKey):
        model, maps = _read(d, key, model_needed=True)
        return Dataset(key, model, maps, comments=_read_comments(d))
    change = _read_json(d / "change.json")
    if change.get("format") != FORMAT:
        raise DatasetError(f"{d} is a dataset of another nolli version; build it again")
    sides = {}
    for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
        if read_key(d / side) != commit:
            raise DatasetError(f"{d / side} is not the {side} of {key.format_key()}")
        sides[side] = read_dataset(d / side)
    model, maps = _read(d, key, model_needed=False)
    if not isinstance(change.get("about"), dict):
        raise DatasetError(f"{d / 'change.json'} says nothing about the change; build it again")
    return Dataset(key, model, maps, change["about"], sides, _read_comments(d))
