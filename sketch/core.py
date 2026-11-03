"""Sketch of nolli's core after the restructuring (2026-11-03). Not wired into nolli.

- Model: structure only. Nodes are addresses with a kind; edges are (src, dst, kind), with
  ``contain`` one of the kinds (a tree); a model made by a transformation refers to the
  elements of its sources.
- Overlay: code that returns the properties of one element. The element carries what an
  overlay needs: its address and kind, what it stands for in the source scenes, and a way
  to ask other overlays. Storage is the overlay's own business (``StoredMaps``).
- Scene: a model, the overlays that apply to it, and the scenes its references point into.
  It hands out elements and caches what overlays answer; overlays never see it.
- Transformations take a scene and return a scene (project, merge). Exporters take a scene.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import cached_property
from typing import Callable, Iterable, Iterator, Mapping, Protocol, Union

FORMAT = 1
CONTAIN = "contain"

Address = str


@dataclass(frozen=True, order=True)
class EdgeKey:
    """An edge's address: its two ends and its kind."""

    src: Address
    dst: Address
    kind: str


Key = Union[Address, EdgeKey]


@dataclass(eq=False)
class Model:
    """Structure only: which nodes exist and of what kind, and which edges connect them."""

    key: str
    kinds: dict[Address, str]
    edges: set[EdgeKey]
    # refs[role][key] = the keys in that source scene this element stands for
    refs: dict[str, dict[Key, tuple[Key, ...]]] = field(default_factory=dict)

    @cached_property
    def _parent(self) -> dict[Address, Address]:
        return {e.dst: e.src for e in self.edges if e.kind == CONTAIN}

    def find_parent(self, a: Address) -> Address | None:
        return self._parent.get(a)

    def iter_ancestors(self, a: Address) -> Iterator[Address]:
        p = self._parent.get(a)
        while p is not None:
            yield p
            p = self._parent.get(p)

    def has(self, key: Key) -> bool:
        return key in self.edges if isinstance(key, EdgeKey) else key in self.kinds


class StoredMaps(Protocol):
    """One overlay's maps in the store, by model key; handed out by ``MapStore.open_maps(name)``,
    so an overlay sees only the maps saved under its name."""

    def load(self, model_key: str) -> Mapping[Key, dict]: ...

    def save(self, model_key: str, values: Mapping[Key, dict]) -> None: ...


class MapStore(Protocol):
    def open_maps(self, name: str) -> StoredMaps: ...


class Element:
    """A node or edge of a scene, as an overlay sees it."""

    def __init__(self, scene: "Scene", key: Key) -> None:
        self._scene = scene
        self.key = key
        self.model_key = scene.model.key

    @property
    def kind(self) -> str:
        return self.key.kind if isinstance(self.key, EdgeKey) else self._scene.model.kinds[self.key]

    @property
    def is_edge(self) -> bool:
        return isinstance(self.key, EdgeKey)

    @cached_property
    def sources(self) -> dict[str, tuple["Element", ...]]:
        """What this element stands for, as elements of the source scenes: ``source`` for a
        projection, ``base`` and ``head`` for a merge, nothing for an extracted model."""
        return {role: tuple(src.find_element(k) for k in self._scene.model.refs.get(role, {}).get(self.key, ()))
                for role, src in self._scene.sources.items()}

    def get(self, overlay: str) -> dict | None:
        return self._scene._compute(self, overlay)

    def __repr__(self) -> str:
        return f"Element({self.key!r} in {self.model_key})"


class Overlay(Protocol):
    name: str

    def compute(self, element: Element) -> dict | None: ...


class OverlayCycle(Exception):
    pass


class Scene:
    def __init__(self, model: Model, overlays: Iterable[Overlay], sources: Mapping[str, "Scene"] | None = None) -> None:
        self.model = model
        self.overlays = {o.name: o for o in overlays}
        self.sources = dict(sources or {})
        self._elements: dict[Key, Element] = {}
        self._cache: dict[tuple[str, Key], dict | None] = {}
        self._busy: set[tuple[str, Key]] = set()

    def find_element(self, key: Key) -> Element:
        if key not in self._elements:
            if not self.model.has(key):
                raise KeyError(key)
            self._elements[key] = Element(self, key)
        return self._elements[key]

    def list_elements(self) -> list[Element]:
        return [self.find_element(k) for k in [*sorted(self.model.kinds), *sorted(self.model.edges)]]

    def _compute(self, element: Element, overlay: str) -> dict | None:
        k = (overlay, element.key)
        if k in self._cache:
            return self._cache[k]
        if k in self._busy:
            raise OverlayCycle(f"{overlay} needs itself for {element.key}")
        self._busy.add(k)
        try:
            value = self.overlays[overlay].compute(element) if overlay in self.overlays else None
        finally:
            self._busy.discard(k)
        self._cache[k] = value
        return value


# --- Transformations --------------------------------------------------------------------


def _derive_key(kind: str, *parts: str) -> str:
    return f"{kind}:{hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]}"


def project(scene: Scene, expanded: set[Address], keep: Callable[[Element], bool] = lambda e: True) -> Scene:
    """Fold the model: a node is drawn if its container is drawn and expanded; everything
    under a folded node is represented by it, and edges are lifted to what is drawn. Each
    drawn node keeps its address and refers to every node it stands for (itself first)."""
    m = scene.model
    kept = {a for a in m.kinds
            if keep(scene.find_element(a)) and all(keep(scene.find_element(p)) for p in m.iter_ancestors(a))}

    def find_drawn(a: Address) -> Address:
        path = [a, *m.iter_ancestors(a)][::-1]
        drawn = path[0]
        for nxt in path[1:]:
            if drawn not in expanded:
                break
            drawn = nxt
        return drawn

    drawn_of = {a: find_drawn(a) for a in kept}
    refs: dict[Key, list[Key]] = {}
    for a in sorted(kept, key=lambda a: (a != drawn_of[a], a)):
        refs.setdefault(drawn_of[a], []).append(a)
    drawn = set(refs)
    edges = {EdgeKey(p, a, CONTAIN) for a in drawn if (p := next((p for p in m.iter_ancestors(a) if p in drawn), None))}
    for e in sorted(m.edges):
        if e.kind != CONTAIN and e.src in kept and e.dst in kept:
            lifted = EdgeKey(drawn_of[e.src], drawn_of[e.dst], e.kind)
            if lifted.src != lifted.dst:
                edges.add(lifted)
                refs.setdefault(lifted, []).append(e)
    model = Model(_derive_key("project", m.key, *sorted(expanded)), {a: m.kinds[a] for a in drawn}, edges,
                  {"source": {k: tuple(v) for k, v in refs.items()}})
    return Scene(model, scene.overlays.values(), {"source": scene})


def merge(base: Scene, head: Scene) -> Scene:
    """The model of a change: every node and edge of either side, referring to its
    counterpart in base and in head (same address = same thing)."""
    kinds = {**base.model.kinds, **head.model.kinds}
    edges = base.model.edges | head.model.edges
    refs = {role: {k: (k,) for k in [*s.model.kinds, *s.model.edges]} for role, s in (("base", base), ("head", head))}
    model = Model(_derive_key("merge", base.model.key, head.model.key), kinds, edges, refs)
    return Scene(model, {**base.overlays, **head.overlays}.values(), {"base": base, "head": head})


# --- Exporter -------------------------------------------------------------------------------


def export_json(scene: Scene) -> dict:
    """Every element with every overlay's properties."""

    def describe(el: Element) -> dict:
        return {n: v for n in scene.overlays if (v := el.get(n)) is not None}

    nodes, edges = [], []
    for el in scene.list_elements():
        if el.is_edge:
            edges.append({"src": el.key.src, "dst": el.key.dst, "kind": el.kind, **describe(el)})
        else:
            nodes.append({"id": el.key, "kind": el.kind, "parent": scene.model.find_parent(el.key), **describe(el)})
    return {"format": FORMAT, "model": scene.model.key, "nodes": nodes, "edges": edges}
