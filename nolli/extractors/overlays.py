"""The extractors' overlays: one stored overlay per extractor that said something (the model
names them, ``meta["overlays"]``), each in its own words; what every language says alike is in
``nolli/model/vocabulary.py``."""

from __future__ import annotations

from nolli.model import Model
from nolli.model.scene import Overlay
from nolli.store.maps import MapStore, StoredOverlay


def make_base_overlays(model: Model, maps: MapStore) -> list[Overlay]:
    """The overlays of an extracted model: one per extractor that said something (the model
    names them, ``meta["overlays"]``), and ``facts`` over all of them."""
    names = model.meta.get("overlays") or []
    return [StoredOverlay(n, maps.open_maps(n)) for n in names]


