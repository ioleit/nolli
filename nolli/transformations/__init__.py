"""Transformations between scenes."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from nolli.model import Model
from nolli.model.scene import Overlay, Scene

Transformation = Callable[..., "Model | tuple[Model, Overlay]"]


def apply(scene: Scene | Mapping[str, Scene], fn: Transformation) -> Scene:
    """The scene ``fn`` makes of ``scene``: ``fn(scene)`` for one scene (role ``source``), ``fn(scenes)``
    for several by role. ``fn`` returns the result's model, whose ``refs`` name the sources'
    elements by role, and optionally an overlay of what it decided. The result keeps its
    sources' overlays and meta, and the scenes as its sources."""
    sources = {"source": scene} if isinstance(scene, Scene) else dict(scene)
    made = fn(scene)
    model, own = made if isinstance(made, tuple) else (made, None)
    meta: dict = {}
    for s in sources.values():
        meta.update(s.model.meta)
    model.meta = {**meta, **model.meta}
    overlays = [o for s in sources.values() for o in s.overlays.values()]
    return Scene(model, [*overlays, *([own] if own is not None else [])], sources)
