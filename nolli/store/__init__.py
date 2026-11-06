"""The interface of a storage for models, changes and their maps.

It defines what a store does and depends on the data model only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from nolli.model import Key, Model
from nolli.model.keys import ChangeKey, CommitKey, ModelKey, NotAllowed, Repo  # noqa: F401 (NotAllowed: part of the protocol)
from nolli.store.maps import MapStore, MemoryMaps, StoredMaps, StoredOverlay  # noqa: F401
from nolli.store.precomputed import CachingOverlay, CachingStrategy, PrecomputedOverlay  # noqa: F401

FORMAT = 1  # of key.json and change.json


class ModelStore(MapStore, Protocol):
    """Keeps models by key (a commit's or a change's) and the maps overlays keep for them.

    ``save_model`` keeps a commit's model with the maps its extractors made; ``save_change``
    keeps a change's own record (what it says about itself, its two sides and the maps kept
    for the change, git's hunks and a review's verdicts among them); ``open_maps`` gives an
    overlay a handle on its maps.

    Everything stored carries a format; data of another format is not read (a load returns
    None, as if nothing were kept).
    """

    def check(self, repo: Repo) -> None:
        """Raises NotAllowed unless the current request may read ``repo``.

        This is the one place that decides who may read what: every read of a model, a change
        or a map goes through it. A store that restricts access overrides ``check`` and nothing
        else."""

    def find_key(self, name: str) -> ModelKey | None:
        """The key of the stored model called ``name`` (``key.format_name()``, or a name it was
        stored under), None if there is none. URLs carry names."""

    def list_names(self) -> list[str]:
        """The names of the stored models this request may read, newest first."""

    def load_model(self, key: CommitKey) -> Model | None:
        """A commit's model, None if there is none (or of another format). Raises NotAllowed if
        this request may not read ``key.repo``."""

    def save_model(self, key: CommitKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        """Keep a commit's model and the maps its extractors made, replacing what was kept."""

    def load_change(self, key: ChangeKey) -> dict | None:
        """What a change says about itself (title, pr, description, …), None if it is not kept.
        Raises NotAllowed as load_model."""

    def save_change(self, key: ChangeKey, about: dict, sides: Mapping[str, tuple[Model, Mapping[str, Mapping[Key, dict]]]],
                    maps: Mapping[str, Mapping[Key, dict]]) -> None:
        """Keep a change: what it says about itself, its two commits' models (``base``, ``head``:
        model and maps, kept with it so a change is all in one place) and the maps kept for the
        change itself. Maps saved for it since (a review's verdicts) stay."""




from nolli.store.directory import DirectoryMaps, DirectoryStore  # noqa: F401 (nolli's own store, as callers import it)
