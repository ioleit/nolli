"""Reads a scene's structure, files, references and changes.

Folding a scene reads nothing else, so anything new to read off a scene for it belongs here."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from urllib.parse import unquote

from nolli.model import vocabulary
from nolli.model.model import Entity, Key, Relation
from nolli.model.scene import Scene
from nolli.model.schema import RelationKind
from nolli.model.vocabulary import FileFacts, Reference

CONF_RANK = {"exact": 0, "heuristic": 1, "statistical": 2}


@dataclass
class Rel:
    """An edge with the references it stands for."""

    key: Relation
    count: int
    confidence: str
    references: list[Reference] = field(default_factory=list)
    diff: dict | None = None  # in a change: {"status", "base", "head", …}; None when unchanged

    @property
    def kind(self) -> str:
        return self.key.kind

    @property
    def src(self) -> str:
        return self.key.src

    @property
    def dst(self) -> str:
        return self.key.dst

    @property
    def subkind(self) -> str:
        return self.key.subkind


class Reading:
    """A scene, read for folding it. The structure comes from the model, what a file or
    directory is from the file tree (``file``), the references behind each edge from the
    extractor that found it (``relations``), and in a change what changed from the
    ``change`` overlay (``diff``)."""

    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.model = scene.model
        self.is_change = "base" in scene.sources

    # -- structure ---------------------------------------------------------------

    def has(self, x: str) -> bool:
        return x in self.model.entities

    def kind(self, x: str) -> str:
        return self.model.entities[x].kind

    def parent(self, x: str) -> str | None:
        return self.model.find_parent(x)

    def children(self, x: str) -> list[str]:
        return self.model.list_children(x)

    def roots(self) -> list[str]:
        return self.model.list_roots()

    def ancestors(self, x: str) -> Iterator[str]:
        return self.model.iter_ancestors(x)

    def descendants(self, x: str) -> Iterator[str]:
        return self.model.iter_descendants(x)

    @property
    def nodes(self) -> dict[str, Entity]:
        return self.model.entities

    def segment(self, x: str) -> str:
        """Return the last step of a node's address: a file's or directory's name, or a service's host."""
        if x.is_repo:
            return x.path.rsplit("/", 1)[-1] if x.path != "." else ""
        return unquote(x.split(":", 1)[1].rsplit("/", 1)[-1])

    # -- what the file tree says ---------------------------------------------------

    @cached_property
    def files(self) -> Mapping[str, FileFacts]:
        return vocabulary.read_all_files(self.scene)

    def file(self, x: str) -> FileFacts | dict:
        return self.files.get(x) or {}

    # -- what changed ------------------------------------------------------------

    @cached_property
    def changes(self) -> Mapping[Key, dict]:
        """What the change did to each node and edge (the ``change`` overlay); {} outside a change."""
        return self.scene.read_all("change") if self.is_change else {}

    def diff(self, x: str) -> dict:
        """Return what the change did to a node, or {} outside a change."""
        return self.changes.get(x) or {}

    # -- edges -------------------------------------------------------------------

    @cached_property
    def relations(self) -> list[Rel]:
        references = vocabulary.read_all_references(self.scene)
        out = []
        for e in self.model.edges:
            if e.kind == RelationKind.CONTAIN:
                continue
            refs = references.get(e, [])
            conf = max((r["confidence"] for r in refs), key=lambda c: CONF_RANK.get(c, 0), default="exact")
            d = self.changes.get(e)
            out.append(Rel(e, sum(r["count"] for r in refs), conf, refs,
                           d if d and d.get("status") in ("added", "removed", "changed") else None))
        return out

    def find_unresolved_language(self, x: str) -> str:
        """Return the language an unresolved reference was written in, as its extractor says, else the
        sort of reference its address names (``http``)."""
        return vocabulary.read_external(self.scene.find_element(x)).get("language") or x.split(":")[1]
