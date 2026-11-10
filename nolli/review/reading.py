"""Reads what a review needs off a change's scene."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property

from nolli.model import vocabulary
from nolli.model.model import Entity, Key, Relation
from nolli.model.scene import Scene
from nolli.model.schema import RelationKind


@dataclass
class ChangedEdge:
    key: Relation
    count: int
    diff: dict | None

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


class Reader:
    """Utilities for reviewing a Scene representing a change."""

    def __init__(self, scene: Scene) -> None:
        self.scene, self.model = scene, scene.model
        self._attrs: dict[str, dict] = {}

    def has(self, x: str) -> bool:
        return x in self.model.entities

    def kind(self, x: str) -> str:
        return self.model.entities[x].kind

    def parent(self, x: str) -> str | None:
        return self.model.find_parent(x)

    def children(self, x: str) -> list[str]:
        return self.model.list_children(x)

    def ancestors(self, x: str) -> Iterator[str]:
        return self.model.iter_ancestors(x)

    def descendants(self, x: str) -> Iterator[str]:
        return self.model.iter_descendants(x)

    @property
    def nodes(self) -> dict[str, Entity]:
        return self.model.entities

    def name(self, x: str) -> str:
        return self.names[x]["name"]

    @cached_property
    def names(self) -> Mapping[str, dict]:
        return self.scene.read_all("names")

    @cached_property
    def files(self) -> Mapping[str, dict]:
        return vocabulary.read_all_files(self.scene)

    @cached_property
    def symbols(self) -> Mapping[str, dict]:
        return vocabulary.read_all_symbols(self.scene)

    @cached_property
    def changes(self) -> Mapping[Key, dict]:
        return self.scene.read_all("change")

    def attrs(self, x: str) -> dict:
        """Where it is (path, line range) and what role it plays: the file tree's and its language's word."""
        a = self._attrs.get(x)
        if a is None:
            a = self._attrs[x] = {**self.files.get(x, {}), **self.symbols.get(x, {})}
        return a

    def diff(self, x: str) -> dict:
        return self.changes.get(x) or {}

    @cached_property
    def relations(self) -> list[ChangedEdge]:
        references = vocabulary.read_all_references(self.scene)
        out = []
        for e in self.model.edges:
            if e.kind == RelationKind.CONTAIN:
                continue
            d = self.changes.get(e)
            out.append(ChangedEdge(e, sum(r["count"] for r in references.get(e, ())),
                                   d if d and d.get("status") in ("added", "removed", "changed") else None))
        return out
