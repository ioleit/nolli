"""nolli's data model: the facts about one codebase, and what such facts may be.

Three levels, a module each:

- a *model* is the graph of one codebase: entities and typed relations with provenance,
  held by ``Model`` (here) and saved as a dataset's ``model.json``;
- the *metamodel* says what any model may contain: kinds, namespaces, which relations
  connect which namespaces (``metamodel.py``); ``Model`` checks every fact against it;
- an *address* is how an entity's id is spelled in its namespace (``address.py``).

Relations are stored at the finest level an extractor could resolve. Coarser
edges are derived by rolling up through ``contain`` (see ``rollup.py``), never
stored. Every tuple carries provenance (extractor, path, line) and a
confidence class: ``exact`` (resolved by construction), ``heuristic`` (matched
by name or pattern) or ``statistical`` (inferred from history).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import address, metamodel
from .metamodel import (  # noqa: F401 (the vocabulary, as callers import it)
    ALLOCATE,
    ARTIFACT_KINDS,
    COMPONENT_KINDS,
    CONTAIN,
    ENTITY_KINDS,
    INTERFACE_KINDS,
    OTHER_KINDS,
    PROVIDE,
    RELATION_KINDS,
    SYMBOL_KINDS,
    USE,
)


@dataclass
class Provenance:
    extractor: str
    path: str | None = None
    line: int | None = None


@dataclass
class Entity:
    id: str
    kind: str
    name: str
    attrs: dict = field(default_factory=dict)
    provenance: Provenance | None = None


@dataclass
class Relation:
    kind: str
    src: str
    dst: str
    subkind: str = ""
    count: int = 1
    confidence: str = "exact"
    provenance: Provenance | None = None
    attrs: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.src, self.dst, self.subkind)


class Model:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.relations: list[Relation] = []
        self.meta: dict = {}
        self._parent: dict[str, str] | None = None
        self._children: dict[str, list[str]] | None = None

    # -- building ---------------------------------------------------------

    def add_entity(self, e: Entity) -> Entity:
        metamodel.check_entity(e.id, address.scheme_of(e.id), address.is_root(e.id), e.kind)
        existing = self.entities.get(e.id)
        if existing:
            if existing.kind != e.kind:
                raise ValueError(f"{e.id} is a {existing.kind}, not a {e.kind}")
            existing.attrs.update({k: v for k, v in e.attrs.items() if k not in existing.attrs})
            return existing
        self.entities[e.id] = e
        self._parent = self._children = None
        return e

    def add_relation(self, r: Relation) -> Relation:
        if r.src not in self.entities or r.dst not in self.entities:
            missing = r.src if r.src not in self.entities else r.dst
            raise KeyError(f"relation {r.kind} {r.src} -> {r.dst}: unknown entity {missing}")
        metamodel.check_relation(r.kind, r.src, r.dst, address.scheme_of(r.src), address.scheme_of(r.dst))
        if r.kind == CONTAIN:
            self._parent = self._children = None
        self.relations.append(r)
        return r

    def ensure_root(self, scheme: str, extractor: str) -> str:
        """The root of a namespace (``metamodel.py``), added if missing."""
        rid, ns = address.root(scheme), metamodel.NAMESPACES[scheme]
        if rid not in self.entities:
            self.add_entity(Entity(rid, ns.root_kind, ns.title, {"synthetic": True}, Provenance(extractor)))
        return rid

    def contain(self, parent: str, child: str, extractor: str) -> None:
        self.add_relation(Relation(CONTAIN, parent, child, provenance=Provenance(extractor)))

    def has(self, entity_id: str) -> bool:
        return entity_id in self.entities

    # -- the containment forest ----------------------------------------------

    def _index(self) -> None:
        if self._parent is not None:
            return
        parent: dict[str, str] = {}
        children: dict[str, list[str]] = defaultdict(list)
        for r in self.relations:
            if r.kind == CONTAIN:
                if r.dst in parent and parent[r.dst] != r.src:
                    raise ValueError(f"{r.dst} has two owners: {parent[r.dst]} and {r.src}")
                parent[r.dst] = r.src
                children[r.src].append(r.dst)
        for v in children.values():
            v.sort()
        self._parent, self._children = parent, children

    def parent(self, entity_id: str) -> str | None:
        self._index()
        return self._parent.get(entity_id)  # type: ignore[union-attr]

    def children(self, entity_id: str) -> list[str]:
        self._index()
        return self._children.get(entity_id, [])  # type: ignore[union-attr]

    def roots(self) -> list[str]:
        self._index()
        return sorted(e for e in self.entities if e not in self._parent)  # type: ignore[operator]

    def ancestors(self, entity_id: str) -> Iterator[str]:
        p = self.parent(entity_id)
        while p is not None:
            yield p
            p = self.parent(p)

    def descendants(self, entity_id: str) -> Iterator[str]:
        stack = list(self.children(entity_id))
        while stack:
            c = stack.pop()
            yield c
            stack.extend(self.children(c))

    def relations_of(self, kinds: Iterable[str] = (USE, PROVIDE, ALLOCATE)) -> list[Relation]:
        ks = set(kinds)
        return [r for r in self.relations if r.kind in ks]

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "meta": self.meta,
            "entities": [asdict(e) for e in sorted(self.entities.values(), key=lambda e: e.id)],
            "relations": [asdict(r) for r in self.relations],
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=1, sort_keys=False))

    @classmethod
    def load(cls, path: Path) -> "Model":
        return cls.from_dict(json.loads(path.read_text()))

    @classmethod
    def from_dict(cls, d: dict) -> "Model":
        m = cls()
        m.meta = d.get("meta", {})
        for e in d["entities"]:
            prov = e.pop("provenance", None)
            m.add_entity(Entity(**e, provenance=Provenance(**prov) if prov else None))
        for r in d["relations"]:
            prov = r.pop("provenance", None)
            m.add_relation(Relation(**r, provenance=Provenance(**prov) if prov else None))
        return m

    def summary(self) -> dict:
        by_kind: dict[str, int] = defaultdict(int)
        for e in self.entities.values():
            by_kind[e.kind] += 1
        rel: dict[str, int] = defaultdict(int)
        for r in self.relations:
            rel[f"{r.kind}/{r.subkind}" if r.subkind else r.kind] += 1
        return {"entities": dict(sorted(by_kind.items())), "relations": dict(sorted(rel.items()))}
