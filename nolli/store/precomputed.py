"""Overlays answered from values a store keeps, and keeping them there.

An overlay's values for one model can be computed once for the whole scene and kept in the
store under the overlay's name and the model's key, together with the overlay's version
(``about``). A ``PrecomputedOverlay`` reads them back and uses the kept value where it is
acceptable. Otherwise it falls back in one of three ways.

- ``strictly_precomputed_overlay`` has no fallback. It is for values about the elements of
  one particular model, such as a projection's or a change's, which never carry over to an
  element of the same address in another model.
- ``caching_overlay`` falls back to the overlay that kept the values, which computes the rest.
- ``precomputed_overlay`` falls back to the source scene's value for the element of the same
  address (``SourceElementOverlay``). It is for values that are facts about an address, such
  as what extraction found, which also hold in a model made from the one they were kept for.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from enum import Enum

from typing import Protocol

from nolli.model.model import Key, Model
from nolli.model.scene import Element, Overlay, Scene, StoredMaps


class KeptMaps(StoredMaps, Protocol):
    """A store's handle on the maps of one name: what an overlay reads, and ``save``, which
    only the store's own methods call."""

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None: ...


class CachingStrategy(Enum):
    """How a precomputed overlay with a fallback treats the store: for now it only reads what was precomputed."""

    USE_PRECOMPUTED_VALUES = "use_precomputed_values"


def _describe_values(overlay: Overlay, by: dict | None = None) -> dict:
    """Return a map's ``about``: which overlay said it, in which version and when, plus ``by``
    (who ran it) if given."""
    return {**(by or {}), "overlay": overlay.name, "cache_version": overlay.cache_version,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _precompute(stored: KeptMaps, scene: Scene, overlay: Overlay, by: dict | None = None) -> Mapping[Key, dict]:
    """Compute what ``overlay`` says about every element of ``scene``, keep it in ``stored`` under
    the scene's model key in place of what was kept, and return the values. The overlay answers
    on the scene with itself attached (in place of one of the same name), so what it asks of
    other overlays, and of the scenes the model was made from, is answered as usual."""
    values = scene.with_overlays(overlay).read_all(overlay.name)
    stored.save(scene.model.key.format_key(), values, _describe_values(overlay, by))
    return values


class SourceElementOverlay(Overlay):
    """The value of overlay ``name`` in the scene's single source, for the element of the same
    address. It is the fallback of an overlay whose values are facts about an address.

    An element of a model made from one scene, such as a projection or a proposed grouping,
    stands for the element of the same address in that scene. An element with no such
    counterpart, such as a box's own code or a proposed directory, gets nothing. So does a
    scene with no source or with several, since a model made from several scenes defines how
    their values combine (``merge.SidesSaid``). It computes no values of its own, so it has no
    version and accepts whatever was kept."""

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
        """Return the values kept for a model if they are acceptable, else None. They are acceptable
        if this version of the fallback kept them, if they were kept without a version (as an
        extractor's are), or, for an overlay without a version of its own, whoever kept them.
        The answer is remembered per model."""
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
        kept = self.find_kept(scene.model.key.format_key())
        if kept is not None:
            return kept
        return {} if self.fallback is None else self.fallback.compute_all(scene)


class OverlayStore:
    """What a store that keeps maps (``open_maps``) does with precomputed overlays: the methods
    every store shares, over its handles."""

    def open_maps(self, name: str) -> KeptMaps:  # the store's own
        raise NotImplementedError

    def list_map_names(self, model_key: str) -> list[str]:
        """Return the names of the maps kept for a model."""
        raise NotImplementedError

    def precompute_overlay_values(self, scene: Scene, overlay: Overlay, by: dict | None = None) -> Mapping[Key, dict]:
        """Keep what ``overlay`` says about ``scene``'s model, for a precomputed overlay to
        answer from."""
        return _precompute(self.open_maps(overlay.name), scene, overlay, by)

    def strictly_precomputed_overlay(self, name: str) -> PrecomputedOverlay:
        """Return an overlay that answers with what was kept under ``name`` for an element's model,
        and nothing else."""
        return PrecomputedOverlay(name, self.open_maps(name))

    def precomputed_overlay(self, name: str) -> PrecomputedOverlay:
        """Return an overlay that answers with what was kept under ``name`` for an element's model,
        or else with what its namesake has in the one scene the model was made from
        (``SourceElementOverlay``). Use it for values that are facts about an address, such as
        what extraction said."""
        return PrecomputedOverlay(name, self.open_maps(name), SourceElementOverlay(name))

    def caching_overlay(self, overlay: Overlay, strategy: CachingStrategy = CachingStrategy.USE_PRECOMPUTED_VALUES) -> PrecomputedOverlay:
        """``overlay``, answered from what this store kept for a model where it is this
        version's, computed elsewhere."""
        return PrecomputedOverlay(overlay.name, self.open_maps(overlay.name), overlay, strategy)

    def load_model(self, key) -> Model | None:  # the store's own, by its kind of key
        raise NotImplementedError

    def open_scene(self, key, overlays: Iterable[Overlay] = ()) -> Scene | None:
        """Open a kept model as a scene, or return None if the store does not have it. Each of
        ``overlays`` is attached as a caching overlay, and every other map kept for the model as
        a strictly precomputed overlay."""
        model = self.load_model(key)
        if model is None:
            return None
        own = [self.caching_overlay(o) for o in overlays]
        named = {o.name for o in own}
        kept = [self.strictly_precomputed_overlay(n) for n in self.list_map_names(model.key.format_key()) if n not in named]
        return Scene(model, [*kept, *own])

    def save_scene(self, scene: Scene, by: dict | None = None) -> None:
        """Keep the scene's model (``save_model``) and what every overlay on it says about it."""
        self.save_model(scene.model)  # type: ignore[attr-defined]
        for o in scene.overlays.values():
            self.precompute_overlay_values(scene, o, by)
