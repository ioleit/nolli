"""Maps kept in memory, for ``MemoryStore``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from nolli.model.model import Key
from nolli.store import precomputed

Maps = dict[str, dict[Key, dict]]  # overlay name -> what it says per node and edge of one model


class _HoldsMaps(Protocol):
    maps: dict[str, dict[str, dict[Key, dict]]]  # name -> model key -> values
    about: dict[tuple[str, str], dict]


class MemoryHandle:
    """A memory store's handle on its maps of one name."""

    def __init__(self, store: _HoldsMaps, name: str) -> None:
        self.store, self.name = store, name

    def load(self, model_key: str) -> Mapping[Key, dict]:
        return self.store.maps.get(self.name, {}).get(model_key, {})

    def save(self, model_key: str, values: Mapping[Key, dict], about: dict | None = None) -> None:
        self.store.maps.setdefault(self.name, {})[model_key] = dict(values)
        self.store.about[(self.name, model_key)] = precomputed.stamp(about)

    def load_about(self, model_key: str) -> dict | None:
        return self.store.about.get((self.name, model_key))
