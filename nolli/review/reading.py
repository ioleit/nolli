"""What the review reads off a change: the structure from the model, where a file or class is
and what role it plays (the file tree, its language), how the picture names it (formats), and
what changed (the ``change`` overlay)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property

from ..formats.descriptions.names import format_name
from ..model import CONTAIN, EdgeKey
from ..model.scene import Scene
from ..model.vocabulary import count_references, read_file, read_symbols


@dataclass
class ChangedEdge:
    key: EdgeKey
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
    def __init__(self, scene: Scene) -> None:
        self.scene, self.model = scene, scene.model
        self._attrs: dict[str, dict] = {}

    def has(self, x: str) -> bool:
        return x in self.model.kinds

    def kind(self, x: str) -> str:
        return self.model.kinds[x]

    def parent(self, x: str) -> str | None:
        return self.model.find_parent(x)

    def children(self, x: str) -> list[str]:
        return self.model.list_children(x)

    def ancestors(self, x: str) -> Iterator[str]:
        return self.model.iter_ancestors(x)

    def descendants(self, x: str) -> Iterator[str]:
        return self.model.iter_descendants(x)

    @property
    def nodes(self) -> dict[str, str]:
        return self.model.kinds

    def name(self, x: str) -> str:
        return format_name(self.scene.find_element(x))

    def attrs(self, x: str) -> dict:
        """Where it is (path, line range) and what role it plays: the file tree's and its language's word."""
        a = self._attrs.get(x)
        if a is None:
            el = self.scene.find_element(x)
            a = self._attrs[x] = {**read_file(el), **read_symbols(el)}
        return a

    def diff(self, x: str) -> dict:
        return self.scene.find_element(x).get("change") or {}

    @cached_property
    def relations(self) -> list[ChangedEdge]:
        out = []
        for e in self.model.edges:
            if e.kind == CONTAIN:
                continue
            el = self.scene.find_element(e)
            d = el.get("change")
            out.append(ChangedEdge(e, count_references(el), d if d and d.get("status") in ("added", "removed", "changed") else None))
        return out
