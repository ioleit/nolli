"""Sketch of nolli's core after the restructuring (2026-11-03). Not wired into nolli.

- Model: structure only. Nodes are addresses with a container, edges are (src, dst, kind),
  and a model made by a transformation refers to the elements of its sources.
- Overlay: code that returns the properties of one element of a scene. It may read a map
  the store keeps for the model, ask other overlays, or ask the source scenes.
- Scene: a model, the overlays that apply to it, and the scenes its references point into.
- Transformations take a scene and return a scene (project, merge). Exporters take a scene.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Mapping, Protocol, Union

FORMAT = 1

Address = str


@dataclass(frozen=True, order=True)
class EdgeKey:
    """An edge's address: its two ends and its kind."""

    src: Address
    dst: Address
    kind: str


Element = Union[Address, EdgeKey]


@dataclass(eq=False)
class Model:
    """Structure only: which nodes exist, what contains what, which edges exist."""

    key: str
    parent: dict[Address, Address | None]
    edges: set[EdgeKey]
    # refs[source name][element] = the elements of that source this element stands for
    refs: dict[str, dict[Element, tuple[Element, ...]]] = field(default_factory=dict)
    _children: dict[Address, list[Address]] | None = field(default=None, repr=False)

    def list_nodes(self) -> Iterable[Address]:
        return self.parent.keys()

    def has(self, element: Element) -> bool:
        return element in self.edges if isinstance(element, EdgeKey) else element in self.parent

    def list_children(self, a: Address) -> list[Address]:
        if self._children is None:
            self._children = {}
            for n, p in self.parent.items():
                if p is not None:
                    self._children.setdefault(p, []).append(n)
        return self._children.get(a, [])

    def iter_ancestors(self, a: Address) -> Iterator[Address]:
        p = self.parent.get(a)
        while p is not None:
            yield p
            p = self.parent.get(p)


class Overlay(Protocol):
    name: str

    def compute(self, element: Element, scene: "Scene") -> dict | None: ...


class MapStore(Protocol):
    """Where overlays keep what is expensive to compute: one map per (model key, overlay)."""

    def load_map(self, model_key: str, overlay: str) -> Mapping[Element, dict] | None: ...

    def save_map(self, model_key: str, overlay: str, values: Mapping[Element, dict]) -> None: ...


class OverlayCycle(Exception):
    pass


class Scene:
    def __init__(self, model: Model, overlays: Iterable[Overlay], sources: Mapping[str, "Scene"] | None = None,
                 store: MapStore | None = None) -> None:
        self.model = model
        self.overlays = {o.name: o for o in overlays}
        self.sources = dict(sources or {})
        self.store = store
        self._cache: dict[tuple[str, Element], dict | None] = {}
        self._busy: set[tuple[str, Element]] = set()
        self._maps: dict[str, Mapping[Element, dict] | None] = {}

    def get(self, element: Element, overlay: str) -> dict | None:
        """One overlay's properties of an element, computed once per scene."""
        k = (overlay, element)
        if k in self._cache:
            return self._cache[k]
        if k in self._busy:
            raise OverlayCycle(f"{overlay} needs itself for {element}")
        self._busy.add(k)
        try:
            value = self.overlays[overlay].compute(element, self) if overlay in self.overlays else None
        finally:
            self._busy.discard(k)
        self._cache[k] = value
        return value

    def list_properties(self, element: Element) -> dict[str, dict]:
        return {n: v for n in self.overlays if (v := self.get(element, n)) is not None}

    def list_source_elements(self, element: Element, source: str = "source") -> list[Element]:
        return list(self.model.refs.get(source, {}).get(element, ()))

    def load_map(self, overlay: str) -> Mapping[Element, dict] | None:
        if overlay not in self._maps:
            self._maps[overlay] = self.store.load_map(self.model.key, overlay) if self.store else None
        return self._maps[overlay]


class StoredOverlay:
    """Properties from the map the store keeps for this model. An element a transformation
    made gets what `combine` makes of its source elements' properties."""

    def __init__(self, name: str) -> None:
        self.name = name

    def compute(self, element: Element, scene: Scene) -> dict | None:
        stored = scene.load_map(self.name)
        if stored is not None and element in stored:
            return stored[element]
        parts = [(name, e, v) for name, src in scene.sources.items()
                 for e in scene.list_source_elements(element, name)
                 if (v := src.get(e, self.name)) is not None]
        return self.combine(element, parts, scene) if parts else None

    def combine(self, element: Element, parts: list[tuple[str, Element, dict]], scene: Scene) -> dict | None:
        return None


# --- Transformations --------------------------------------------------------------------


def _derive_key(kind: str, *parts: str) -> str:
    return f"{kind}:{hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]}"


def project(scene: Scene, expanded: set[Address], keep: Callable[[Scene, Address], bool] = lambda s, a: True) -> Scene:
    """Fold the model: a node is drawn if its container is drawn and expanded; everything
    under a folded node is represented by it, and edges are lifted to what is drawn. Each
    drawn node keeps its own address and refers to every node it stands for (itself first)."""
    m = scene.model
    kept = {a for a in m.list_nodes() if keep(scene, a) and all(keep(scene, p) for p in m.iter_ancestors(a))}

    def find_drawn(a: Address) -> Address:
        path = [a, *m.iter_ancestors(a)][::-1]
        drawn = path[0]
        for nxt in path[1:]:
            if drawn not in expanded:
                break
            drawn = nxt
        return drawn

    drawn_of = {a: find_drawn(a) for a in kept}
    node_refs: dict[Element, list[Element]] = {}
    for a in sorted(kept, key=lambda a: (a != drawn_of[a], a)):
        node_refs.setdefault(drawn_of[a], []).append(a)
    edges: set[EdgeKey] = set()
    edge_refs: dict[Element, list[Element]] = {}
    for e in sorted(m.edges):
        if e.src in kept and e.dst in kept:
            lifted = EdgeKey(drawn_of[e.src], drawn_of[e.dst], e.kind)
            if lifted.src != lifted.dst:
                edges.add(lifted)
                edge_refs.setdefault(lifted, []).append(e)
    drawn = set(node_refs)
    parent = {a: next((p for p in m.iter_ancestors(a) if p in drawn), None) for a in drawn}
    refs = {k: tuple(v) for k, v in {**node_refs, **edge_refs}.items()}
    key = _derive_key("project", m.key, *sorted(expanded))
    return Scene(Model(key, parent, edges, {"source": refs}), scene.overlays.values(), {"source": scene}, scene.store)


def merge(base: Scene, head: Scene) -> Scene:
    """The model of a change: every node and edge of either side, referring to its
    counterpart in base and in head (same address = same thing)."""
    parent = {**base.model.parent, **head.model.parent}
    edges = base.model.edges | head.model.edges
    refs = {side: {e: (e,) for e in [*s.model.list_nodes(), *s.model.edges]} for side, s in (("base", base), ("head", head))}
    key = _derive_key("merge", base.model.key, head.model.key)
    overlays = {**base.overlays, **head.overlays}.values()
    return Scene(Model(key, parent, edges, refs), overlays, {"base": base, "head": head}, head.store)


# --- Exporter -------------------------------------------------------------------------------


def export_json(scene: Scene) -> dict:
    m = scene.model
    return {
        "format": FORMAT,
        "model": m.key,
        "nodes": [{"id": a, "parent": m.parent[a], **scene.list_properties(a)} for a in sorted(m.list_nodes())],
        "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind, **scene.list_properties(e)} for e in sorted(m.edges)],
    }
