"""Merge: two models of one repository, base and head, into the model of the change between
them. Same address, same thing: the change holds every node and edge of either side, and
each refers to its namesake in ``base`` and in ``head`` (where it has one). Nothing else is
decided here; what changed is the ``change`` overlay's to say (``nolli/overlays/change.py``),
from what the two sides' overlays say and from git's hunks (the ``content`` overlay).
"""

from __future__ import annotations

from ..model import CONTAIN, Model
from ..scene import Overlay, Scene


def merge(base: Scene, head: Scene, key: str, meta: dict | None = None, overlays: list[Overlay] = ()) -> Scene:
    """The change from ``base`` to ``head`` as a scene: the overlays of both sides (head's of a
    name win) and ``overlays``."""
    kinds = {**base.model.kinds, **head.model.kinds}
    owned = {e.dst for e in head.model.edges if e.kind == CONTAIN}
    edges = list(head.model.edges) + [e for e in base.model.edges if not (e.kind == CONTAIN and e.dst in owned)]
    model = Model(key, kinds, edges, refs={"base": None, "head": None}, meta=dict(meta or {}), check=False)
    return Scene(model, [*base.overlays.values(), *head.overlays.values(), *overlays], {"base": base, "head": head})
