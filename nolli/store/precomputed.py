"""Overlays answered from values a store keeps, and keeping them there.

An overlay's values for one model are precomputed by asking it about the whole scene once
and keeping the answer under the overlay's name and the model's key, with which version of
the overlay said it (``about``). Read back, they come as a ``PrecomputedOverlay``: what was
kept, where it is acceptable, and what its fallback says otherwise. Three fallbacks, three
ways to read: nothing (``strictly_precomputed_overlay``: a picture's or a change's word about
its own elements, kept under its own key and never taken from a namesake in another model);
the overlay that kept the values, computing the rest (``caching_overlay``); and the source's
word for the element of the same address (``precomputed_overlay``, ``SourceElementOverlay``),
for values that are facts about an address, what extraction said, which hold for a namesake
in a model made from the one they were kept for.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from enum import Enum

from typing import Protocol

from nolli.model import Key, Model
from nolli.model.scene import Element, Overlay, Scene, StoredMaps


class KeptMaps(StoredMaps, Protocol):
    """A store's handle on the maps of one name: what an overlay reads, and ``save``, which
    only the store's own methods call."""

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None: ...


class CachingStrategy(Enum):
    """How a precomputed overlay with a fallback treats the store: for now it only reads what was precomputed."""

    USE_PRECOMPUTED_VALUES = "use_precomputed_values"


def _describe_values(overlay: Overlay, by: dict | None = None) -> dict:
    """A map's ``about``: which overlay said it, in which version, when (and ``by``: who ran it)."""
    return {**(by or {}), "overlay": overlay.name, "cache_version": overlay.cache_version,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _precompute(stored: KeptMaps, scene: Scene, overlay: Overlay, by: dict | None = None) -> Mapping[Key, dict]:
    """What ``overlay`` says about every element of ``scene``, kept in ``stored`` under the
    scene's model key, replacing what was kept; the values. The overlay answers on the scene
    with itself attached (in place of one of the same name), so what it asks of other
    overlays, and of the scenes the model was made from, is answered as usual."""
    values = scene.with_overlays(overlay).read_all(overlay.name)
    stored.save(scene.model.key, values, _describe_values(overlay, by))
    return values


class SourceElementOverlay(Overlay):
    """What the scene's one source says under ``name`` about the element of the same address:
    the fallback of an overlay read by address. Same address, same thing: an element of a
    model made from one scene (a picture, a proposed grouping) stands for its namesake there,
    and nothing is said for one that has no namesake (a box's own code, a proposed directory).
    On a scene with no source, or several, nothing: a model made from several scenes says
    itself how its sources' words combine (``merge.SidesSaid``). It makes no values of its own,
    so it has no version: whatever was kept is acceptable."""

    cache_version = ""

    def __init__(self, name: str) -> None:
        self.name = name

    def compute(self, element: Element) -> dict | None:
        if len(element.scene.sources) != 1:
            return None
        same = element.find_same()
        return None if same is None else same.get(self.name)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        if len(scene.sources) != 1:
            return {}
        (src,) = scene.sources.values()
        return {k: v for k, v in src.read_all(self.name).items() if scene.model.has(k)}


class PrecomputedOverlay(Overlay):
    """What was kept under ``name`` for the element's model, where an acceptable version kept
    it, and what ``fallback`` says otherwise: nothing without one, the overlay's own answer
    for the overlay that kept the values (``caching_overlay``), the source's word for a
    namesake (``SourceElementOverlay``). The fallback says which kept values are acceptable
    (its ``cache_version``; without a version of its own, or without a fallback, whatever was
    kept is), and answers where nothing is. Values are kept once per model; the fallback's
    own compute runs with this overlay in its place, so what it asks of itself elsewhere (a
    drawn box's sources, say) is answered from the store where it can be."""

    def __init__(self, name: str, stored: StoredMaps, fallback: Overlay | None = None,
                 strategy: CachingStrategy = CachingStrategy.USE_PRECOMPUTED_VALUES) -> None:
        self.name, self.stored, self.fallback, self.strategy = name, stored, fallback, strategy
        self._kept: dict[str, Mapping[Key, dict] | None] = {}  # model key -> acceptable values, None for none

    @property
    def cache_version(self) -> str:
        return self.fallback.cache_version if self.fallback is not None else ""

    def find_kept(self, model_key: str) -> Mapping[Key, dict] | None:
        """The values kept for a model, if they are acceptable: kept by this version of the
        fallback, or without a version (as an extractor's are), or by anything at all for an
        overlay without a version of its own."""
        if model_key not in self._kept:
            values = self.stored.load(model_key)
            about = self.stored.load_about(model_key) if values else None
            stale = bool(self.cache_version) and about is not None and about.get("cache_version") not in ("", None, self.cache_version)
            self._kept[model_key] = None if not values or stale else values
        return self._kept[model_key]

    def compute(self, element: Element) -> dict | None:
        kept = self.find_kept(element.model_key)
        if kept is not None:
            return kept.get(element.key)
        return None if self.fallback is None else self.fallback.compute(element)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        kept = self.find_kept(scene.model.key)
        if kept is not None:
            return kept
        return {} if self.fallback is None else self.fallback.compute_all(scene)


class OverlayStore:
    """What a store that keeps maps (``open_maps``) does with precomputed overlays: the methods
    every store shares, over its handles."""

    def open_maps(self, name: str) -> KeptMaps:  # the store's own
        raise NotImplementedError

    def list_map_names(self, model_key: str) -> list[str]:
        """The names of the maps kept for a model."""
        raise NotImplementedError

    def precompute_overlay_values(self, scene: Scene, overlay: Overlay, by: dict | None = None) -> Mapping[Key, dict]:
        """Keep what ``overlay`` says about ``scene``'s model, for a precomputed overlay to
        answer from."""
        return _precompute(self.open_maps(overlay.name), scene, overlay, by)

    def strictly_precomputed_overlay(self, name: str) -> PrecomputedOverlay:
        """The overlay that says what was kept under ``name`` for an element's model, and nothing else."""
        return PrecomputedOverlay(name, self.open_maps(name))

    def precomputed_overlay(self, name: str) -> PrecomputedOverlay:
        """The overlay that says what was kept under ``name`` for an element's model, or for its
        namesake in the one scene the model was made from (``SourceElementOverlay``): for
        values that are facts about an address, what extraction said."""
        return PrecomputedOverlay(name, self.open_maps(name), SourceElementOverlay(name))

    def caching_overlay(self, overlay: Overlay, strategy: CachingStrategy = CachingStrategy.USE_PRECOMPUTED_VALUES) -> PrecomputedOverlay:
        """``overlay``, answered from what this store kept for a model where it is this
        version's, computed elsewhere."""
        return PrecomputedOverlay(overlay.name, self.open_maps(overlay.name), overlay, strategy)

    def load_model(self, key) -> Model | None:  # the store's own, by its kind of key
        raise NotImplementedError

    def open_scene(self, key, overlays: Iterable[Overlay] = (), sources: Mapping[str, Scene] | None = None) -> Scene | None:
        """A kept model as a scene, None if the store does not have it: each of ``overlays`` as
        a caching overlay, and every other map kept for the model as strictly precomputed."""
        model = self.load_model(key)
        if model is None:
            return None
        own = [self.caching_overlay(o) for o in overlays]
        named = {o.name for o in own}
        kept = [self.strictly_precomputed_overlay(n) for n in self.list_map_names(model.key) if n not in named]
        return Scene(model, [*kept, *own], sources)

    def save_scene(self, scene: Scene, by: dict | None = None) -> None:
        """Keep the scene's model (``save_model``) and what every overlay on it says about it."""
        self.save_model(scene.model)  # type: ignore[attr-defined]
        for o in scene.overlays.values():
            self.precompute_overlay_values(scene, o, by)
