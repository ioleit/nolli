"""Utilities to store and retrieve overlays as maps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from nolli.model.keys import ModelKey
from nolli.model.model import Key, Model
from nolli.store import precomputed
from nolli.store.precomputed import KeptMaps, OverlayStore

Maps = dict[str, dict[Key, dict]]  # overlay name -> what it says per node and edge of one model


class MapStore(Protocol):
    """Keeps maps. A map is kept by model key (``Model.key``) and holds, per node or edge, what
    an overlay says about it, with ``about`` (who made it, when)."""

    def open_maps(self, name: str) -> KeptMaps:
        """Open a handle on the maps kept under ``name`` and nothing else, so an overlay never sees
        the store itself."""
        ...




class MemoryMaps(OverlayStore):
    """Maps in memory (a model just extracted, tests), and models under any key."""

    def __init__(self, maps: dict[str, dict[str, dict[Key, dict]]] | None = None) -> None:
        self.maps = maps if maps is not None else {}  # name -> model key -> values
        self.about: dict[tuple[str, str], dict] = {}
        self.models: dict[ModelKey, Model] = {}

    def open_maps(self, name: str) -> MemoryHandle:
        return MemoryHandle(self, name)

    def list_map_names(self, model_key: str) -> list[str]:
        return sorted(n for n, by_model in self.maps.items() if model_key in by_model)

    def load_model(self, key: ModelKey) -> Model | None:
        return self.models.get(key)

    def save_model(self, model: Model) -> None:
        self.models[model.key] = model


class MemoryHandle:
    def __init__(self, store: MemoryMaps, name: str) -> None:
        self.store, self.name = store, name

    def load(self, model_key: str) -> Mapping[Key, dict]:
        return self.store.maps.get(self.name, {}).get(model_key, {})

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None:
        self.store.maps.setdefault(self.name, {})[model_key] = dict(values)
        self.store.about[(self.name, model_key)] = precomputed.stamp(about)

    def load_about(self, model_key: str) -> dict | None:
        return self.store.about.get((self.name, model_key))
