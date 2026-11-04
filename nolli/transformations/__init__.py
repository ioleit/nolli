"""Transformations: a scene made of other scenes. A transformation is a function that, given its
source scene (or scenes, by role), returns the model of the result (and, if it decided
something about its elements, that as an overlay); ``apply`` makes the scene of it. The model
refers to the source's elements by role (``Model.refs``: ``source`` for one scene, the roles of
the dict for several, None for "the same key there"). The result has its sources' overlays,
its model carries their meta under its own, and their scenes are its sources.

nolli's own: ``projection.fold`` (fold and filter a scene, ``projection.py``), ``merge.union``
(two commits into the change between them, ``merge.py``); a selection is ``select``. As in
``transformations.apply(scene, projection.fold(spec))``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from nolli.model import Key, Model
from nolli.model.scene import Overlay, Scene

Transformation = Callable[..., "Model | tuple[Model, Overlay]"]


def apply(scene: Scene | Mapping[str, Scene], fn: Transformation) -> Scene:
    """The scene ``fn`` makes of ``scene``: ``fn(scene)`` for one scene (role ``source``), ``fn(scenes)``
    for several by role."""
    sources = {"source": scene} if isinstance(scene, Scene) else dict(scene)
    made = fn(scene)
    model, own = made if isinstance(made, tuple) else (made, None)
    meta: dict = {}
    for s in sources.values():
        meta.update(s.model.meta)
    model.meta = {**meta, **model.meta}
    overlays = [o for s in sources.values() for o in s.overlays.values()]
    return Scene(model, [*overlays, *([own] if own is not None else [])], sources)


def select(scene: Scene, keep: set[Key]) -> Scene:
    """The part of ``scene`` with the elements ``keep`` names (edges too, between kept nodes)."""
    return apply(scene, lambda s: s.model.select(keep))
