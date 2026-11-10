"""The structure of one codebase, as nodes and the edges between them.

A model holds structure only: what else is known about a node or an edge belongs in overlays.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from nolli.model.keys import ModelKey, StringKey
from nolli.model.schema import EntityId, EntityKind, RelationKind


@dataclass(frozen=True)
class Entity:
    """A node: its address (``id``, an ``EntityId``), which is its identity, and its kind
    (``EntityKind``), which is not: two entities are the same when their addresses are."""

    id: EntityId  # given as text too; one that is no address raises
    kind: EntityKind = field(compare=False)  # given as its name too ("file"); an unknown one raises

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", EntityId(self.id))
        try:
            object.__setattr__(self, "kind", EntityKind(self.kind))
        except ValueError:
            raise ValueError(f"unknown entity kind {self.kind!r}") from None


@dataclass(frozen=True, order=True)
class Relation:
    """An edge, which is also its address: its kind, its two ends and its subkind (how a use
    was found: ``import``, ``ref``, ``http``, …)."""

    kind: RelationKind  # given as its name too ("use"); an unknown one raises
    src: EntityId  # given as text too
    dst: EntityId
    subkind: str = ""
    _hash: int = field(init=False, repr=False, compare=False, default=0)  # computed once: a key is hashed on every lookup

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", RelationKind(self.kind))
        except ValueError:
            raise ValueError(f"unknown relation kind {self.kind!r}") from None
        object.__setattr__(self, "src", EntityId(self.src))
        object.__setattr__(self, "dst", EntityId(self.dst))
        object.__setattr__(self, "_hash", hash((self.kind, self.src, self.dst, self.subkind)))

    def __hash__(self) -> int:
        return self._hash

    def format(self) -> str:
        """``kind|src|dst|subkind``: how the viewer, verdicts and changes name an edge."""
        return f"{self.kind}|{self.src}|{self.dst}|{self.subkind}"

    @classmethod
    def parse(cls, s: str) -> Relation:
        kind, src, dst, sub = s.split("|", 3)
        return cls(kind, src, dst, sub)


Key = EntityId | Relation  # a node's address, or an edge's


class Model:
    """Structure: ``entities`` (by address), ``edges`` (relations), and for a model made
    by a transformation (a change, a projection) ``refs`` (role -> element -> the elements of
    that source it stands for). ``contain`` edges form the ownership tree. ``key`` names the
    model (what it is the model of, a ``ModelKey``); its maps are kept by its text (``format_key``).

    Everything else known about a node or an edge (its name, size, role, a docstring, which
    lines an import is on, a review's verdict) is in overlays, never in the model. Unless
    ``check`` is False, every edge's ends must be among its entities."""

    def __init__(self, key: ModelKey = StringKey(), entities: Iterable[Entity] = (), edges: Iterable[Relation] = (),
                 refs: dict[str, dict[Key, tuple[Key, ...]]] | None = None, meta: dict | None = None,
                 check: bool = True) -> None:
        self.key = key
        self.entities: dict[EntityId, Entity] = {x.id: x for x in entities}
        self.edges: list[Relation] = sorted(set(edges))
        self.refs = refs or {}
        self.meta: dict = meta or {}
        self._edge_set = set(self.edges)
        parent: dict[str, str] = {}
        children: dict[str, list[str]] = defaultdict(list)
        out: dict[str, list[Relation]] = defaultdict(list)
        into: dict[str, list[Relation]] = defaultdict(list)
        for e in self.edges:
            if check:
                if e.src not in self.entities or e.dst not in self.entities:
                    raise KeyError(f"edge {e.format()}: unknown node {e.src if e.src not in self.entities else e.dst}")
            if e.kind == RelationKind.CONTAIN:
                if e.dst in parent and parent[e.dst] != e.src:
                    raise ValueError(f"{e.dst} has two owners: {parent[e.dst]} and {e.src}")
                parent[e.dst] = e.src
                children[e.src].append(e.dst)
            else:
                out[e.src].append(e)
                into[e.dst].append(e)
        for v in children.values():
            v.sort()
        self._parent, self._children, self._out, self._in = parent, dict(children), dict(out), dict(into)
        self._roots = sorted(x for x in self.entities if x not in parent)

    # -- structure -----------------------------------------------------------

    def has(self, key: Key) -> bool:
        return key in self._edge_set if isinstance(key, Relation) else key in self.entities

    def find_kind(self, key: Key) -> str:
        return key.kind if isinstance(key, Relation) else self.entities[key].kind

    def find_parent(self, x: str) -> str | None:
        return self._parent.get(x)

    def list_children(self, x: str) -> list[str]:
        return self._children.get(x, [])

    def list_roots(self) -> list[str]:
        return self._roots

    def iter_ancestors(self, x: str) -> Iterator[str]:
        p = self._parent.get(x)
        while p is not None:
            yield p
            p = self._parent.get(p)

    def iter_descendants(self, x: str) -> Iterator[str]:
        stack = list(self.list_children(x))
        while stack:
            c = stack.pop()
            yield c
            stack.extend(self.list_children(c))

    def list_edges(self, kinds: Iterable[str] | None = None) -> list[Relation]:
        """Edges other than ``contain`` (or of ``kinds``)."""
        ks = set(kinds) if kinds is not None else None
        return [e for e in self.edges if (e.kind in ks if ks is not None else e.kind != RelationKind.CONTAIN)]

    def list_out(self, x: str) -> list[Relation]:
        """Edges (but ``contain``) from ``x``."""
        return self._out.get(x, [])

    def list_in(self, x: str) -> list[Relation]:
        return self._in.get(x, [])

    def count_nodes_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for x in self.entities.values():
            out[x.kind] += 1
        return dict(sorted(out.items()))
