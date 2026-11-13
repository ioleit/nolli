"""Storage for models, changes, their maps and comments.

``ModelStore`` (``model_store.py``) is what a store does; it depends on the data model only. Two
stores implement it: ``MemoryStore`` (``memory.py``) and ``RepoDirectoryStore``, by repository on
disk, with ``RepoStoreProvider`` to open one (``repo_directory.py``). What is built on the maps a
store keeps, overlays answered from them, is in ``precomputed.py``: functions that take any store."""

from nolli.model.keys import ChangeKey, CommitKey, ModelKey, NotAllowed  # noqa: F401 (NotAllowed: a store may raise it)
from nolli.model.scene import StoredMaps  # noqa: F401
from nolli.store.precomputed import CachingStrategy, KeptMaps, PrecomputedOverlay, SourceElementOverlay  # noqa: F401
from nolli.store.model_store import ModelStore  # noqa: F401
from nolli.store.memory import MemoryStore  # noqa: F401 (a ModelStore kept nowhere else)
from nolli.store.repo_directory import RepoDirectoryStore, RepoStoreProvider  # noqa: F401 (nolli's own, by repository)
