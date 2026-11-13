"""Cuts a scene down to a part of it."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from nolli.model.keys import ModelKey, SelectionKey
from nolli.model.model import Entity, Key, Model, Relation
from nolli.model.scene import Scene


def select(scene: Scene, keep: Iterable[Key]) -> Scene:
    """Return the part of ``scene`` with the nodes ``keep`` names and the edges between them (an
    edge in ``keep`` too). Each element stands for itself in ``scene``, its ``source``."""
    model, keep = scene.model, set(keep)
    entities = [x for x in model.entities.values() if x.id in keep]
    edges = [e for e in model.edges if e.src in keep and e.dst in keep]
    part = Model(_make_key(model.key, entities, edges), entities, edges, check=False)
    return Scene(part, [], {"source": scene}, {"source": None})


def _make_key(source: ModelKey, entities: Iterable[Entity], edges: Iterable[Relation]) -> SelectionKey:
    """Return the key of the part of the model ``source`` with ``entities`` and ``edges``: the
    source's key and a digest of their ids, so the same part always has the same key. The ids
    can be many, so the key carries their digest rather than the ids."""
    ids = sorted([*(str(x.id) for x in entities), *(str(e.id) for e in edges)])
    return SelectionKey(source, hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16])
