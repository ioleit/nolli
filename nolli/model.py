"""The fact model: entities and typed relations, with provenance.

The vocabulary is the UML 2 component/deployment subset:

- entities are artifacts (directory, file), components (service, image),
  interfaces (endpoint, env, host) or external packages;
- relations are ``contain`` (ownership, a tree), ``use`` (Parnas's uses),
  ``provide`` (interface realization) and ``allocate`` (software element to
  environment element: a directory copied into an image, an image run as a
  service).

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

CONTAIN, USE, PROVIDE, ALLOCATE = "contain", "use", "provide", "allocate"
RELATION_KINDS = (CONTAIN, USE, PROVIDE, ALLOCATE)

# Entity kinds by UML metaclass. ``group`` is a synthetic container the tool
# adds so that interfaces, externals and deployment elements have a place in
# the containment forest; it has no counterpart in the repository.
ARTIFACT_KINDS = ("directory", "file")
SYMBOL_KINDS = ("class", "function")  # module-viewtype elements inside a file
COMPONENT_KINDS = ("service", "image")
INTERFACE_KINDS = ("api", "resource", "endpoint", "env", "host")  # api > resource > endpoint: extractors/api.py
OTHER_KINDS = ("package", "group", "unresolved")
ENTITY_KINDS = ARTIFACT_KINDS + SYMBOL_KINDS + COMPONENT_KINDS + INTERFACE_KINDS + OTHER_KINDS


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
        if e.kind not in ENTITY_KINDS:
            raise ValueError(f"unknown entity kind {e.kind!r}")
        existing = self.entities.get(e.id)
        if existing:
            existing.attrs.update({k: v for k, v in e.attrs.items() if k not in existing.attrs})
            return existing
        self.entities[e.id] = e
        self._parent = self._children = None
        return e

    def add_relation(self, r: Relation) -> Relation:
        if r.kind not in RELATION_KINDS:
            raise ValueError(f"unknown relation kind {r.kind!r}")
        if r.src not in self.entities or r.dst not in self.entities:
            missing = r.src if r.src not in self.entities else r.dst
            raise KeyError(f"relation {r.kind} {r.src} -> {r.dst}: unknown entity {missing}")
        if r.kind == CONTAIN:
            self._parent = self._children = None
        self.relations.append(r)
        return r

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
        d = json.loads(path.read_text())
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
