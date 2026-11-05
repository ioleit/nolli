"""What extraction builds: entities and relations with their attributes, as the extractors
write them. ``Facts`` is mutable and checked against the metamodel as it grows; when
extraction is done, ``split()`` turns it into the structure (``nolli.model.Model``) and, per
extractor, what that extractor said about each node and edge (its overlay's stored map,
``nolli/model/scene.py``).

Which extractor said what: an entity's attributes are recorded under the extractor that
wrote each of them (``Facts.current`` while it runs, so a language's symbols extractor
adding ``doc`` to a file the file tree made is the symbols extractor's word), a relation
under the extractor its provenance names. Every tuple carries provenance (extractor,
path, line) and a confidence class: ``exact`` (resolved by construction), ``heuristic``
(matched by name or pattern) or ``statistical`` (inferred from history).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from nolli.model import EdgeKey, Key, Model, address, metamodel


@dataclass
class Provenance:
    extractor: str
    path: str | None = None
    line: int | None = None


class Attrs(dict):
    """An entity's attributes, remembering which extractor wrote each."""

    def __init__(self, facts: Facts, data: dict, by: str) -> None:
        super().__init__(data)
        self.facts = facts
        self.by = {k: by for k in data}

    def __setitem__(self, k, v) -> None:
        super().__setitem__(k, v)
        self.by[k] = self.facts.current

    def setdefault(self, k, v=None):
        if k not in self:
            self[k] = v
        return super().__getitem__(k)

    def update(self, *args, **kw) -> None:
        for k, v in dict(*args, **kw).items():
            self[k] = v

    def __delitem__(self, k) -> None:
        super().__delitem__(k)
        self.by.pop(k, None)

    def pop(self, k, *default):
        self.by.pop(k, None)
        return super().pop(k, *default)


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


class Facts:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.relations: list[Relation] = []
        self.meta: dict = {}
        self.current = "fs"  # the extractor running (build.extract sets it)
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
        e.attrs = Attrs(self, e.attrs, e.provenance.extractor if e.provenance else self.current)
        e.attrs.by["name"] = e.attrs.by.get("name", e.provenance.extractor if e.provenance else self.current)
        self.entities[e.id] = e
        self._parent = self._children = None
        return e

    def add_relation(self, r: Relation) -> Relation:
        if r.src not in self.entities or r.dst not in self.entities:
            missing = r.src if r.src not in self.entities else r.dst
            raise KeyError(f"relation {r.kind} {r.src} -> {r.dst}: unknown entity {missing}")
        metamodel.check_relation(r.kind, r.src, r.dst, address.scheme_of(r.src), address.scheme_of(r.dst))
        if r.kind == metamodel.CONTAIN:
            self._parent = self._children = None
        if r.provenance is None:
            r.provenance = Provenance(self.current)
        self.relations.append(r)
        return r

    def ensure_root(self, scheme: str, extractor: str) -> str:
        """The root of a namespace (``metamodel.py``), added if missing."""
        rid, ns = address.root(scheme), metamodel.NAMESPACES[scheme]
        if rid not in self.entities:
            self.add_entity(Entity(rid, ns.root_kind, ns.title, {"synthetic": True}, Provenance(extractor)))
        return rid

    def contain(self, parent: str, child: str, extractor: str) -> None:
        self.add_relation(Relation(metamodel.CONTAIN, parent, child, provenance=Provenance(extractor)))

    def has(self, entity_id: str) -> bool:
        return entity_id in self.entities

    # -- the containment forest ----------------------------------------------

    def _index(self) -> None:
        if self._parent is not None:
            return
        parent: dict[str, str] = {}
        children: dict[str, list[str]] = defaultdict(list)
        for r in self.relations:
            if r.kind == metamodel.CONTAIN:
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

    def relations_of(self, kinds: Iterable[str] = (metamodel.USE, metamodel.PROVIDE, metamodel.ALLOCATE)) -> list[Relation]:
        ks = set(kinds)
        return [r for r in self.relations if r.kind in ks]

    # -- what extraction made ------------------------------------------------

    def split(self, key: str = "") -> tuple[Model, dict[str, dict[Key, dict]]]:
        """The structure, and per extractor what it said: for a node its attributes (``name``
        among them), for an edge its facts (one per relation: count, confidence, where it
        was found, the relation's attributes) and their total ``count``."""
        maps: dict[str, dict[Key, dict]] = defaultdict(dict)
        for e in self.entities.values():
            by = getattr(e.attrs, "by", {})
            creator = e.provenance.extractor if e.provenance else "fs"
            maps[by.get("name", creator)].setdefault(e.id, {})["name"] = e.name
            for k, v in e.attrs.items():
                maps[by.get(k, creator)].setdefault(e.id, {})[k] = v
        edges = set()
        for r in self.relations:
            k = EdgeKey(r.kind, r.src, r.dst, r.subkind)
            edges.add(k)
            if r.kind == metamodel.CONTAIN:
                continue
            p = r.provenance
            ref = {"count": r.count, "confidence": r.confidence, "path": p.path if p else None, **r.attrs}
            if p and p.line is not None:
                ref["line"] = p.line
            _check_reference(ref, p.extractor if p else "unknown", k)
            maps[p.extractor if p else "unknown"].setdefault(k, {"references": []})["references"].append(ref)
        model = Model(key, {x: e.kind for x, e in self.entities.items()}, edges, meta=dict(self.meta))
        model.meta["overlays"] = sorted(maps)
        return model, {name: dict(v) for name, v in sorted(maps.items())}


def _check_reference(ref: dict, extractor: str, key: EdgeKey) -> None:
    """Raises unless ``ref`` is a ``Reference`` (``nolli/model/vocabulary.py``): every extractor says it
    in the same words, so code that works with any language can count on them."""
    for k in ("count", "confidence", "path"):
        if ref.get(k) is None:
            raise ValueError(f"{extractor}: a reference of {key.format()} without {k}")
    if not isinstance(ref["count"], int) or ("line" in ref and not isinstance(ref["line"], int)):
        raise ValueError(f"{extractor}: a reference of {key.format()} with a count or line that is no number")

