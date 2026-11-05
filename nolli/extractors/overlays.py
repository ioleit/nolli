"""Opens the overlays an extraction stored, one per extractor."""

from __future__ import annotations

from nolli.model import Model
from nolli.model.scene import Overlay
from nolli.store.maps import MapStore, StoredOverlay


def make_base_overlays(model: Model, maps: MapStore) -> list[Overlay]:
    """The overlays of an extracted model: one per extractor that said something (the model
    names them, ``meta["overlays"]``). Each is in that extractor's own words; what every language says alike is in ``nolli/model/vocabulary.py``."""
    names = model.meta.get("overlays") or []
    return [StoredOverlay(n, maps.open_maps(n)) for n in names]


