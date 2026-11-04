"""nolli's data model: the structure of one codebase, and what such structure may be.

Three levels, a module each:

- a *model* is structure only (``Model``, here): nodes, each an address with a kind, and
  edges between them, each a kind (``contain`` among them: the ownership tree) and a
  subkind (how a use was found: ``import``, ``ref``, ``http``, …). Everything else that is
  known about a node or an edge (its name, size, role, a docstring, which lines an import
  is on, a review's verdict) is in *overlays* (``nolli/model/scene.py``), never in the model;
- the *metamodel* says what any model may contain: kinds, namespaces, which relations
  connect which namespaces (``metamodel.py``); an extracted model is checked against it;
- an *address* is how a node's id is spelled in its namespace (``address.py``).

A model made by a transformation (a change, a projection: ``nolli/transformations/``) also refers
to the elements of the models it was made from (``refs``). ``model.json`` holds a model with
its ``format``; a reader refuses another format rather than misread it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Union

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

FORMAT = 1  # of model.json; a model of another format is built again, never read


class FormatError(ValueError):
    """Stored data of a format this version of nolli does not read."""


@dataclass(frozen=True, order=True)
class EdgeKey:
    """An edge's address: its kind, its two ends and its subkind."""

    kind: str
    src: str
    dst: str
    subkind: str = ""

    def format(self) -> str:
        """``kind|src|dst|subkind``: how the viewer, verdicts and changes name an edge."""
        return f"{self.kind}|{self.src}|{self.dst}|{self.subkind}"

    @classmethod
    def parse(cls, s: str) -> "EdgeKey":
        kind, src, dst, sub = s.split("|", 3)
        return cls(kind, src, dst, sub)


Key = Union[str, EdgeKey]  # a node's address, or an edge's


class Model:
    """Structure: ``kinds`` (address -> kind), ``edges``, and for a derived model ``refs``
    (role -> element -> the elements of that source it stands for). ``key`` names the model
    (what it is the model of: ``nolli/store/`` keys); stored overlays are kept by it."""

    def __init__(self, key: str = "", kinds: dict[str, str] | None = None, edges: Iterable[EdgeKey] = (),
                 refs: dict[str, dict[Key, tuple[Key, ...]]] | None = None, meta: dict | None = None,
                 check: bool = True) -> None:
        self.key = key
        self.kinds: dict[str, str] = dict(kinds or {})
        self.edges: list[EdgeKey] = sorted(set(edges))
        self.refs = refs or {}
        self.meta: dict = meta or {}
        self._edge_set = set(self.edges)
        parent: dict[str, str] = {}
        children: dict[str, list[str]] = defaultdict(list)
        out: dict[str, list[EdgeKey]] = defaultdict(list)
        into: dict[str, list[EdgeKey]] = defaultdict(list)
        for e in self.edges:
            if check:
                if e.src not in self.kinds or e.dst not in self.kinds:
                    raise KeyError(f"edge {e.format()}: unknown node {e.src if e.src not in self.kinds else e.dst}")
                metamodel.check_relation(e.kind, e.src, e.dst, address.scheme_of(e.src), address.scheme_of(e.dst))
            if e.kind == CONTAIN:
                if e.dst in parent and parent[e.dst] != e.src:
                    raise ValueError(f"{e.dst} has two owners: {parent[e.dst]} and {e.src}")
                parent[e.dst] = e.src
                children[e.src].append(e.dst)
            else:
                out[e.src].append(e)
                into[e.dst].append(e)
        if check:
            for x, kind in self.kinds.items():
                metamodel.check_entity(x, address.scheme_of(x), address.is_root(x), kind)
        for v in children.values():
            v.sort()
        self._parent, self._children, self._out, self._in = parent, dict(children), dict(out), dict(into)
        self._roots = sorted(x for x in self.kinds if x not in parent)

    # -- structure -----------------------------------------------------------

    def has(self, key: Key) -> bool:
        return key in self._edge_set if isinstance(key, EdgeKey) else key in self.kinds

    def find_kind(self, key: Key) -> str:
        return key.kind if isinstance(key, EdgeKey) else self.kinds[key]

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

    def list_edges(self, kinds: Iterable[str] | None = None) -> list[EdgeKey]:
        """Edges other than ``contain`` (or of ``kinds``)."""
        ks = set(kinds) if kinds is not None else None
        return [e for e in self.edges if (e.kind in ks if ks is not None else e.kind != CONTAIN)]

    def list_out(self, x: str) -> list[EdgeKey]:
        """Edges (but ``contain``) from ``x``."""
        return self._out.get(x, [])

    def list_in(self, x: str) -> list[EdgeKey]:
        return self._in.get(x, [])

    def count_nodes_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for k in self.kinds.values():
            out[k] += 1
        return dict(sorted(out.items()))

    # -- model.json ----------------------------------------------------------

    def to_dict(self) -> dict:
        return {"format": FORMAT, "key": self.key, "meta": self.meta,
                "nodes": dict(sorted(self.kinds.items())),
                "edges": [[e.kind, e.src, e.dst, e.subkind] for e in self.edges]}

    def save(self, path: Path) -> None:
        if self.refs:
            raise ValueError("a derived model is made again from its sources, not saved")
        path.write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def from_dict(cls, d: dict) -> "Model":
        if d.get("format") != FORMAT:
            raise FormatError(f"model.json of format {d.get('format')!r}, this nolli reads {FORMAT}")
        return cls(d.get("key", ""), d["nodes"], (EdgeKey(*e) for e in d["edges"]), meta=d.get("meta") or {})

    @classmethod
    def load(cls, path: Path) -> "Model":
        return cls.from_dict(json.loads(path.read_text()))
