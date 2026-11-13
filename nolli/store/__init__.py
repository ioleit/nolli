"""The interface of a storage for models, changes, their maps and comments.

It defines what a store does and depends on the data model only. What is built on top of the
maps a store keeps, overlays answered from them, is in ``nolli/store/precomputed.py``: functions
that take any store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from nolli.model.keys import ChangeKey, CommitKey, ModelKey, NotAllowed  # noqa: F401 (NotAllowed: a store may raise it)
from nolli.model.model import Key, Model
from nolli.model.scene import StoredMaps  # noqa: F401
from nolli.store.precomputed import CachingStrategy, KeptMaps, PrecomputedOverlay, SourceElementOverlay  # noqa: F401


class ModelStore(Protocol):
    """Keeps models by key (a commit's, a change's, a projection's, …), the maps kept for them,
    what an overlay said about a model (``nolli/store/precomputed.py``), and comments.

    ``save_model`` keeps a model (a commit's) with the maps extraction made; ``save_change``
    keeps a change's own record (what it says about itself, its two sides and the maps kept
    for the change, git's hunks among them); ``open_maps`` is the handle on the maps of one
    name, which an overlay gets instead of the store. ``save_comments`` keeps what people and
    agents said about a model, which outlives building it again.

    Everything stored carries a format; data of another format is not read (a load returns
    None, as if nothing were kept). Who may read what is decided before a store is had: a store
    may keep the models of one repository only and raise NotAllowed for any other key
    (``RepoStoreProvider``, ``nolli/store/repo_directory.py``).
    """

    def open_maps(self, name: str) -> KeptMaps:
        """Open a handle on the maps kept under ``name`` and nothing else, so an overlay never sees
        the store itself. A map can be kept for any key, whether a model is kept under it or not
        (a projection's)."""

    def list_map_names(self, model_key: str) -> list[str]:
        """Return the names of the maps kept for the model with key text ``model_key``."""

    def load_model(self, key: ModelKey) -> Model | None:
        """Return a commit's model, or a change's merged model if it was kept with the change.
        Return None if there is none or it is of another format."""

    def save_model(self, key: ModelKey, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        """Keep a model under ``key`` and the maps its extractors made, replacing what was kept."""

    def load_change(self, key: ChangeKey) -> dict | None:
        """Return what a change says about itself (title, pr, description, …), or None if it is not
        kept."""

    def save_change(self, key: ChangeKey, about: dict, sides: Mapping[str, tuple[Model, Mapping[str, Mapping[Key, dict]]]],
                    maps: Mapping[str, Mapping[Key, dict]], model: Model | None = None) -> None:
        """Keep a change: what it says about itself, its two commits' models (``base``, ``head``:
        model and maps, kept with it), the maps kept for the
        change itself, and its merged model (``model``), so that it is not merged again on every
        read. The comments on it stay."""

    def load_comments(self, key: ModelKey) -> list[dict]:
        """Return the comments kept on the model of ``key`` (``nolli/logic/comments.py``), or []."""

    def save_comments(self, key: ModelKey, comments: list[dict]) -> None:
        """Keep ``comments`` on the model of ``key``, in place of those kept before."""



from nolli.store.memory import MemoryStore  # noqa: E402, F401 (a ModelStore kept nowhere else)
from nolli.store.repo_directory import RepoDirectoryStore, RepoStoreProvider  # noqa: E402, F401 (nolli's own, by repository)
