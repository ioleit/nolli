"""Transformations between scenes."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from nolli.model.scene import Scene

Transformation = Callable[..., Scene]


def apply(scene: Scene | Mapping[str, Scene], fn: Transformation) -> Scene:
    """Apply a transformation to a scene. Extracted for future refactoring."""
    return fn(scene)
