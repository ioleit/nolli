"""Where nolli keeps what it builds: models, by key (``nolli/keys.py``), and the maps
overlays keep for them (``maps.py``).

``ModelStore`` is the interface: a commit's model with the maps its extractors saved
(``save_model``), a change's own record (``save_change``: what it says about itself, its two
sides and the maps kept for the change, git's hunks and a review's verdicts among them), and
per overlay a handle on its maps (``open_maps``). nolli's own is ``DirectoryStore``
(``directory.py``).

Who may read what is decided in one place, ``check(repo)``: every read of a model, a change
or a map goes through it. nolli's own store lets everyone read everything; a service built
on nolli overrides ``check`` (raising ``NotAllowed``) and nothing else.

Everything stored carries a format; data of another format is not read (it is built again).
The store depends on the data model only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..keys import ChangeKey, Commit, ModelKey, NotAllowed, Repo  # noqa: F401 (NotAllowed: part of the protocol)
from ..model import Key, Model
from .maps import MapStore, MemoryMaps, StoredMaps, StoredOverlay  # noqa: F401

FORMAT = 1  # of key.json and change.json


class ModelStore(MapStore, Protocol):
    def check(self, repo: Repo) -> None:
        """Raises NotAllowed unless the current request may read ``repo``; every read calls it."""

    def find_key(self, name: str) -> ModelKey | None:
        """The key of the stored model called ``name`` (``key.format_name()``, or a name it was
        stored under), None if there is none. URLs carry names."""

    def list_names(self) -> list[str]:
        """The names of the stored models this request may read, newest first."""

    def load_model(self, key: Commit) -> Model | None:
        """A commit's model, None if there is none (or of another format). Raises NotAllowed if
        this request may not read ``key.repo``."""

    def save_model(self, key: Commit, model: Model, maps: Mapping[str, Mapping[Key, dict]]) -> None:
        """Keep a commit's model and the maps its extractors made, replacing what was kept."""

    def load_change(self, key: ChangeKey) -> dict | None:
        """What a change says about itself (title, pr, description, …), None if it is not kept.
        Raises NotAllowed as load_model."""

    def save_change(self, key: ChangeKey, about: dict, sides: Mapping[str, tuple[Model, Mapping[str, Mapping[Key, dict]]]],
                    maps: Mapping[str, Mapping[Key, dict]]) -> None:
        """Keep a change: what it says about itself, its two commits' models (``base``, ``head``:
        model and maps, kept with it so a change is all in one place) and the maps kept for the
        change itself. Maps saved for it since (a review's verdicts) stay."""




from .directory import DirectoryMaps, DirectoryStore, read_key, write_change, write_commit  # noqa: F401
