"""Entities and relations as extractors write them, and who said what."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from nolli.model import vocabulary
from nolli.model.keys import ModelKey, StringKey
from nolli.model.model import Entity, Key, Model, Relation
from nolli.model.schema import EntityId, EntityKind, RelationKind, Scheme
from nolli.progress import Progress, Quiet

ROOT_NAMES = {Scheme.API: "APIs", Scheme.PKG: "Artifacts", Scheme.RUNTIME: "Runtime",  # what each root group is called
              Scheme.CONFIG: "Configuration", Scheme.UNRESOLVED: "Unresolved references"}


@dataclass
class Provenance:
    """Where a fact comes from: the extractor, and the path and line it was found at."""
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
class EntityFact:
    id: EntityId  # given as text too; one that is no address raises
    kind: str
    name: str
    attrs: dict = field(default_factory=dict)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        self.id = EntityId(self.id)


@dataclass
class RelationFact:
    """A relation between two entities. ``confidence`` is ``exact`` (resolved by
    construction), ``heuristic`` (matched by name or pattern) or ``statistical``
    (inferred from history)."""
    kind: str
    src: EntityId  # given as text too
    dst: EntityId
    subkind: str = ""
    count: int = 1
    confidence: str = "exact"
    provenance: Provenance | None = None
    attrs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.src, self.dst = EntityId(self.src), EntityId(self.dst)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.src, self.dst, self.subkind)


class Facts:
    """Entities and relations as the extractors write them, their kinds checked as they
    grow; ``split()`` turns them into a ``Model`` and what was said, by shape.

    An entity's attributes are recorded under the extractor that wrote each of them
    (``current`` while it runs, so a symbols extractor adding ``doc`` to a file the file
    tree made is the symbols extractor's word), a relation under the extractor its
    provenance names."""

    def __init__(self) -> None:
        self.entities: dict[str, EntityFact] = {}
        self.relations: list[RelationFact] = []
        self.meta: dict = {}
        self.current = "fs"  # the extractor running (extract_facts sets it)
        self._parent: dict[str, str] | None = None
        self._children: dict[str, list[str]] | None = None

    # -- building ---------------------------------------------------------

    def add_entity(self, e: EntityFact) -> EntityFact:
        e.kind = EntityKind(e.kind)  # raises unless it is a kind
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

    def add_relation(self, r: RelationFact) -> RelationFact:
        if r.src not in self.entities or r.dst not in self.entities:
            missing = r.src if r.src not in self.entities else r.dst
            raise KeyError(f"relation {r.kind} {r.src} -> {r.dst}: unknown entity {missing}")
        r.kind = RelationKind(r.kind)  # raises unless it is a kind
        if r.kind == RelationKind.CONTAIN:
            self._parent = self._children = None
        if r.provenance is None:
            r.provenance = Provenance(self.current)
        self.relations.append(r)
        return r

    def ensure_root(self, scheme: str, extractor: str) -> str:
        """Return the group at the root of a scheme's addresses (``api:``), adding it if missing and
        naming it for what it holds (``ROOT_NAMES``). The repository's root is its directory instead."""
        rid = EntityId.root(scheme)
        if rid not in self.entities:
            self.add_entity(EntityFact(rid, EntityKind.GROUP, ROOT_NAMES[scheme], {"synthetic": True}, Provenance(extractor)))
        return rid

    def contain(self, parent: str, child: str, extractor: str) -> None:
        self.add_relation(RelationFact(RelationKind.CONTAIN, parent, child, provenance=Provenance(extractor)))

    def has(self, entity_id: str) -> bool:
        return entity_id in self.entities

    # -- the containment forest ----------------------------------------------

    def _index(self) -> None:
        if self._parent is not None:
            return
        parent: dict[str, str] = {}
        children: dict[str, list[str]] = defaultdict(list)
        for r in self.relations:
            if r.kind == RelationKind.CONTAIN:
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

    def relations_of(self, kinds: Iterable[str] = (RelationKind.USE, RelationKind.PROVIDE, RelationKind.ALLOCATE)) -> list[RelationFact]:
        ks = set(kinds)
        return [r for r in self.relations if r.kind in ks]

    # -- what extraction made ------------------------------------------------

    def split(self, key: ModelKey = StringKey()) -> tuple[Model, dict[str, dict[Key, dict]]]:
        """Split the facts into the structure and what the extractors said about it, filed by shape
        (``vocabulary.SHAPES``, one map each, whichever extractor spoke). For a node the map holds
        its attributes (``name`` among them), for an edge its facts (one per relation: count,
        confidence, where it was found, the relation's attributes). Each map is what its overlay says."""
        maps: dict[str, dict[Key, dict]] = defaultdict(dict)
        for e in self.entities.values():
            by = getattr(e.attrs, "by", {})
            creator = e.provenance.extractor if e.provenance else "fs"
            maps[vocabulary.shape_of(e.id, by.get("name", creator))].setdefault(e.id, {})["name"] = e.name
            for k, v in e.attrs.items():
                maps[vocabulary.shape_of(e.id, by.get(k, creator))].setdefault(e.id, {})[k] = v
        edges = set()
        for r in self.relations:
            k = Relation(r.kind, r.src, r.dst, r.subkind)
            edges.add(k)
            if r.kind == RelationKind.CONTAIN:
                continue
            p = r.provenance
            ref = {"count": r.count, "confidence": r.confidence, "path": p.path if p else None, **r.attrs}
            if p and p.line is not None:
                ref["line"] = p.line
            _check_reference(ref, p.extractor if p else "unknown", k)
            maps[vocabulary.REFERENCES].setdefault(k.id, {"references": []})["references"].append(ref)
        model = Model(key, (Entity(x, e.kind) for x, e in self.entities.items()), edges, meta=dict(self.meta))
        return model, {name: dict(v) for name, v in sorted(maps.items())}


def _check_reference(ref: dict, extractor: str, key: Relation) -> None:
    """Raise ``ValueError`` unless ``ref`` is a ``Reference`` (``nolli/model/vocabulary.py``). Every
    extractor says it in the same words, so code that works with any language can count on them."""
    for k in ("count", "confidence", "path"):
        if ref.get(k) is None:
            raise ValueError(f"{extractor}: a reference of {key.id} without {k}")
    if not isinstance(ref["count"], int) or ("line" in ref and not isinstance(ref["line"], int)):
        raise ValueError(f"{extractor}: a reference of {key.id} with a count or line that is no number")


WORKING_NOTES = ("py-imports", "js-imports", "py-aliases", "ts-exports")  # extractors' state between passes, not kept


def extract_facts(repo: Path, files: list[Path], config: dict | None = None, progress: Progress | None = None) -> Facts:
    """Run every extractor, then the matchers, over ``files`` (relative to ``repo``) and return what
    they found, before it is split into structure and maps. ``meta`` says how many files, the
    languages, the timings and the extractors' notes; the caller adds where the files came from
    (repository, commit)."""
    from nolli.logic import extractors  # extractors/__init__ imports this module
    from nolli.logic.extractors import match

    facts = Facts()
    progress = progress or Quiet()
    ctx = extractors.Context(repo=repo, model=facts, files=files, config=config or {}, progress=progress)
    timings = {}
    for name, fn in extractors.all_extractors():
        t0 = time.time()
        facts.current = name
        with progress.step("file tree" if name == "fs" else name.replace("-", " ")):
            fn(ctx)
        timings[name] = round(time.time() - t0, 3)
    t0 = time.time()
    facts.current = "match"
    with progress.step("matching references"):
        match.run_all(ctx)
    timings["match"] = round(time.time() - t0, 3)
    facts.meta = {"files": len(ctx.files), "languages": extractors.language_names(), "timings": timings,
                  "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "notes": {k: v for k, v in ctx.notes.items() if k not in WORKING_NOTES}}
    return facts
