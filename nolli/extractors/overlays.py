"""The extractors' overlays, for now: one stored overlay per extractor that said something (the
model names them, ``meta["overlays"]``), and ``facts``, all of them together."""

from __future__ import annotations

from collections.abc import Iterable

from ..model import Model
from ..model.scene import Element, Overlay
from ..store.maps import MapStore, StoredOverlay


class FactsOverlay:
    """``facts``: what all extractors said, together (each wrote different attributes; an
    edge's facts and counts add up), for code that does not care which one said it."""

    name = "facts"

    def __init__(self, names: Iterable[str]) -> None:
        self.names = tuple(names)

    def compute(self, element: Element) -> dict | None:
        parts = [v for n in self.names if (v := element.get(n))]
        if not parts:
            same = element.find_same()
            return same.get(self.name) if same is not None else None
        if not element.is_edge:
            out: dict = {}
            for p in parts:
                out.update(p)
            return out
        return {"count": sum(p.get("count", 0) for p in parts), "facts": [f for p in parts for f in p.get("facts", [])]}




def make_base_overlays(model: Model, maps: MapStore) -> list[Overlay]:
    """The overlays of an extracted model: one per extractor that said something (the model
    names them, ``meta["overlays"]``), and ``facts`` over all of them."""
    names = model.meta.get("overlays") or []
    return [*(StoredOverlay(n, maps.open_maps(n)) for n in names), FactsOverlay(names)]


