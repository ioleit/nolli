"""The maps overlays keep in a store, and the overlay for anything kept as a map.

A store gives each overlay a handle restricted to the maps saved under the overlay's name
(``MapStore.open_maps(name)``, a ``StoredMaps``): the overlay never sees the store itself. A
map is kept by model key (``Model.key``) and holds, per node or edge, what the overlay says
about it, with ``about`` (who made it, when).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..model import EdgeKey, FormatError, Key
from ..model.scene import Element

FORMAT = 2  # of a map as JSON (2: an edge is {"references": [...]})


class StoredMaps(Protocol):
    """One overlay's maps in a store, by model key (``MapStore.open_maps(name)``): an overlay
    sees only the maps saved under its name."""

    def load(self, model_key: str) -> Mapping[Key, dict]: ...

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None: ...

    def load_about(self, model_key: str) -> dict | None: ...


class MapStore(Protocol):
    def open_maps(self, name: str) -> StoredMaps: ...




class MemoryMaps:
    """Maps in memory (a model just extracted, tests)."""

    def __init__(self, maps: dict[str, dict[str, dict[Key, dict]]] | None = None) -> None:
        self.maps = maps if maps is not None else {}  # name -> model key -> values
        self.about: dict[tuple[str, str], dict] = {}

    def open_maps(self, name: str) -> _MemoryHandle:
        return _MemoryHandle(self, name)


class _MemoryHandle:
    def __init__(self, store: MemoryMaps, name: str) -> None:
        self.store, self.name = store, name

    def load(self, model_key: str) -> Mapping[Key, dict]:
        return self.store.maps.get(self.name, {}).get(model_key, {})

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None:
        self.store.maps.setdefault(self.name, {})[model_key] = dict(values)
        if about is not None:
            self.store.about[(self.name, model_key)] = about

    def load_about(self, model_key: str) -> dict | None:
        return self.store.about.get((self.name, model_key))




class StoredOverlay:
    """What the store keeps under this name for the element's model: the overlay for anything
    saved as a map, an extractor's word or an annotation from outside. ``follow``: an element
    with nothing stored takes what its namesake in a source has (a projected box what the
    node it is says; a change's node what head, else base, says).

    It does not roll up: a folded box gets nothing from what it stands for, and a drawn edge
    nothing from the edges it bundles. An overlay that should derives from this one and
    combines its sources' answers itself (as ``nolli.review.verdicts`` does). A generic
    combine (concatenating the values, say) may come later."""

    def __init__(self, name: str, stored: StoredMaps, follow: bool = True) -> None:
        self.name, self.stored, self.follow = name, stored, follow

    def compute(self, element: Element) -> dict | None:
        own = self.stored.load(element.model_key).get(element.key)
        if own is not None or not self.follow:
            return own
        same = element.find_same()
        return same.get(self.name) if same is not None else None




def dump_map(model_key: str, values: Mapping[Key, dict], about: dict | None) -> dict:
    nodes = {k: v for k, v in values.items() if not isinstance(k, EdgeKey)}
    edges = {k.format(): v for k, v in values.items() if isinstance(k, EdgeKey)}
    return {"format": FORMAT, "model": model_key, "about": about, "nodes": nodes, "edges": edges}


def parse_map(d: dict) -> tuple[dict[Key, dict], dict | None]:
    if d.get("format") != FORMAT:
        raise FormatError(f"a map of format {d.get('format')!r}, this nolli reads {FORMAT}")
    values: dict[Key, dict] = dict(d.get("nodes") or {})
    values.update({EdgeKey.parse(k): v for k, v in (d.get("edges") or {}).items()})
    return values, d.get("about")


