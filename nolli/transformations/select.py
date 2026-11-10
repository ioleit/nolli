"""Cuts a model down to a part of it."""

from __future__ import annotations

from collections.abc import Iterable

from nolli.model.keys import StringKey
from nolli.model.model import Key, Model


def select(model: Model, keep: Iterable[Key]) -> Model:
    """The part of ``model`` with the nodes ``keep`` names and the edges between them (an
    edge in ``keep`` too), each referring to itself there (``refs``: the same key). A
    transformation's model: ``apply(scene, lambda s: select(s.model, keep))``."""
    keep = set(keep)
    entities = [x for x in model.entities.values() if x.id in keep]
    edges = [e for e in model.edges if e.src in keep and e.dst in keep]
    return Model(StringKey(f"{model.key.format_key()}|select"), entities, edges, refs={"source": None}, check=False)
