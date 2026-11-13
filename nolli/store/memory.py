"""A ModelStore in memory: models, changes, their maps and comments kept nowhere else (tests, examples,
anything built to be looked at once)."""

from __future__ import annotations

from collections.abc import Mapping

from nolli.model.keys import ChangeKey, ModelKey
from nolli.model.model import Key, Model
from nolli.store.maps import MemoryHandle
from nolli.store.precomputed import OverlayStore


class MemoryStore(OverlayStore):
    """Commits' and changes' models, what changes say about themselves, and every map kept for
    them, in memory, under any key."""

    def __init__(self) -> None:
        self.maps: dict[str, dict[str, dict[Key, dict]]] = {}  # name -> model key -> values
        self.about: dict[tuple[str, str], dict] = {}
        self.models: dict[ModelKey, Model] = {}
        self.changes: dict[ChangeKey, dict] = {}
        self.comments: dict[ModelKey, list[dict]] = {}

    def open_maps(self, name: str) -> MemoryHandle:
        return MemoryHandle(self, name)  # type: ignore[arg-type]  # it reads and writes .maps and .about, as on MemoryMaps

    def list_map_names(self, model_key: str) -> list[str]:
        return sorted(n for n, by_model in self.maps.items() if model_key in by_model)

    def load_model(self, key: ModelKey) -> Model | None:
        return self.models.get(key)

    def save_model(self, key: ModelKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        model.key = key
        self.models[key] = model
        for name, values in maps.items():
            self.open_maps(name).save(key.format_key(), values)

    def load_change(self, key: ChangeKey) -> dict | None:
        about = self.changes.get(key)
        return about if about is not None and all(c in self.models for c in key.list_sides()) else None

    def save_change(self, key: ChangeKey, about: dict, sides: Mapping[str, tuple[Model, Mapping[str, Mapping[Key, dict]]]],
                    maps: Mapping[str, Mapping[Key, dict]], model: Model | None = None) -> None:
        for (m, side_maps), commit in zip((sides["base"], sides["head"]), key.list_sides(), strict=True):
            self.save_model(commit, m, side_maps)
        self.changes[key] = dict(about)
        for name, values in maps.items():
            self.open_maps(name).save(key.format_key(), values)
        if model is not None:
            model.key = key
            self.models[key] = model

    def load_comments(self, key: ModelKey) -> list[dict]:
        return list(self.comments.get(key, []))

    def save_comments(self, key: ModelKey, comments: list[dict]) -> None:
        self.comments[key] = list(comments)
