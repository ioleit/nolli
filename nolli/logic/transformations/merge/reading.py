"""Reads a change's scene by node: names, places and roles, and what changed in each node and edge."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property

from nolli.model import schema, vocabulary
from nolli.model.model import Entity, Key, Relation
from nolli.model.scene import Scene
from nolli.model.schema import EntityId, RelationKind


@dataclass
class ChangedEdge:
    relation: Relation
    count: int
    diff: dict | None

    @property
    def kind(self) -> str:
        return self.relation.kind

    @property
    def src(self) -> str:
        return self.relation.src

    @property
    def dst(self) -> str:
        return self.relation.dst

    @property
    def subkind(self) -> str:
        return self.relation.subkind


class Reader:
    """Read a change's scene by node: its tree, what extraction said about each node, and what
    the change did to each node and edge."""

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
            d = self.changes.get(e.id)
            out.append(ChangedEdge(e, sum(r["count"] for r in references.get(e.id, ())),
                                   d if d and d.get("status") in ("added", "removed", "changed") else None))
        return out


def find_module(g: Reader, x: str) -> str:
    """Return the module (file) a node belongs to; a node outside files is returned as it is."""
    cur: str | None = x
    while cur is not None:
        if g.has(cur) and g.kind(cur) == "file":
            return cur
        cur = g.parent(cur)
    return x


def format_name(g: Reader, x: str) -> str:
    """Return a readable name for a node. Files and directories are named by their path, classes
    (and the other symbols) by path#Name."""
    if not g.has(x):
        return x
    kind, a = g.kind(x), g.attrs(x)
    if kind in ("file", "directory"):
        return a.get("path") or g.name(x)
    if kind in schema.SYMBOL_KINDS:
        return f"{a.get('path')}#{g.name(x)}"
    return g.name(x)


def list_ancestors_to_unfold(g: Reader, x: str) -> list[str]:
    """List the boxes to unfold so that x itself is drawn, outermost first."""
    return list(g.ancestors(x))[::-1]


def find_box(scene: Scene, x: str) -> str | None:
    """Find the drawn box that stands for ``x``: ``x`` itself if it is drawn (a module's own code
    too), else the box holding it (or its owner), or None. ``x`` may be text (an address)."""
    x = EntityId(x)
    if scene.has(x):
        return x
    return scene.find_holder(x) or scene.find_holder(x.owner)
