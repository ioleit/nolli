"""The JSON forms of a model (``model.json``) and of a map (what one overlay says about a
model's nodes and edges), as a store and nolli's dataset files write them.

Each form carries its format; a form of another format is refused (``FormatError``), never
misread."""

from __future__ import annotations

from collections.abc import Mapping

from nolli.model import schema
from nolli.model.keys import ModelKey, StringKey
from nolli.model.model import Entity, Key, Model, Relation
from nolli.model.schema import EntityId, RelationId

MODEL_FORMAT = 3  # of model.json; a model of another format is built again, never read (3: its maps are by shape, meta names no overlays)
MAP_FORMAT = 2  # of a map as JSON (2: an edge is {"references": [...]})


class FormatError(ValueError):
    """Stored data of a format this version of nolli does not read."""


def dump_model(model: Model) -> dict:
    """Return a model's structure as written to ``model.json``. What a model made from others
    stands for in them is not written; it belongs to its scene and is worked out again when the
    scene is made (``merge.union`` for a kept change)."""
    return {"format": MODEL_FORMAT, "key": model.key.format_key(), "meta": model.meta,
            "nodes": {x: model.entities[x].kind for x in sorted(model.entities)},
            "edges": [[e.kind, e.src, e.dst, e.subkind] for e in model.edges]}


def parse_model(d: dict, key: ModelKey | None = None) -> Model:
    """Parse a model from its ``model.json`` form, under ``key`` (by default a ``StringKey`` of the
    text kept). Raise ``FormatError`` for another format."""
    if d.get("format") != MODEL_FORMAT:
        raise FormatError(f"model.json of format {d.get('format')!r}, this nolli reads {MODEL_FORMAT}")
    return Model(key or StringKey(d.get("key", "")), (Entity(x, k) for x, k in d["nodes"].items()), (Relation(*e) for e in d["edges"]),
                 meta=d.get("meta") or {})  # a ``refs`` kept by an older nolli is ignored


def dump_map(model_key: str, values: Mapping[Key, dict], about: dict | None) -> dict:
    """Return a map as JSON: its values split into nodes and edges, with the model's key and ``about``."""
    keys = {k: schema.parse_key(k) for k in values}  # ids given as plain text (read from JSON) are told apart by their text
    nodes = {k: v for k, v in values.items() if not isinstance(keys[k], RelationId)}
    edges = {k: v for k, v in values.items() if isinstance(keys[k], RelationId)}
    return {"format": MAP_FORMAT, "model": model_key, "about": about, "nodes": nodes, "edges": edges}


def parse_map(d: dict) -> tuple[dict[Key, dict], dict | None]:
    """Parse a map as ``dump_map`` wrote it, returning its values and its ``about``. The values are
    keyed as they were given, a node by its ``EntityId`` and an edge by its ``RelationId``. Raise
    ``FormatError`` for another format and ``ValueError`` for a node key that is no address."""
    if d.get("format") != MAP_FORMAT:
        raise FormatError(f"a map of format {d.get('format')!r}, this nolli reads {MAP_FORMAT}")
    values: dict[Key, dict] = {EntityId(k): v for k, v in (d.get("nodes") or {}).items()}
    values.update({RelationId(k): v for k, v in (d.get("edges") or {}).items()})
    return values, d.get("about")
