"""Cuts a scene down to a part of it."""

from __future__ import annotations

from collections.abc import Iterable

from nolli.model.keys import StringKey
from nolli.model.model import Key, Model
from nolli.model.scene import Scene


def select(scene: Scene, keep: Iterable[Key]) -> Scene:
    """The part of ``scene`` with the nodes ``keep`` names and the edges between them (an
    edge in ``keep`` too), each standing for itself there (its ``source``)."""
    model, keep = scene.model, set(keep)
    entities = [x for x in model.entities.values() if x.id in keep]
    edges = [e for e in model.edges if e.src in keep and e.dst in keep]
    part = Model(StringKey(f"{model.key.format_key()}|select"), entities, edges, check=False)
    return Scene(part, [], {"source": scene}, {"source": None})
