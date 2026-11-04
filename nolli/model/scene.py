"""Scenes, elements and overlays: how anything is known about a model's nodes and edges.

- An *overlay* is code that says something about one element: ``compute(element)`` returns
  its properties (a dict) or None. The file tree's overlay says a file's role and size, a
  language's says which names a module defines, the change overlay what a change did to a
  box, the verdicts overlay what a review said about it. An overlay decides for itself where
  its knowledge comes from: a map it keeps in the store (``nolli.store.StoredOverlay``, by
  model key, as an extractor saved it), other overlays (``element.get``), or what the element
  stands for in the models it was made from (``element.sources``).
- An *element* is a node or an edge as an overlay sees it: its key and kind, and for an
  edge its two ends; its place in the structure; what it stands for (``sources``: role ->
  elements of the source scenes: ``source`` for a projection, ``base`` and ``head`` for a
  change); and ``get(overlay)``, any overlay's properties of it.
- A *scene* is a model, the overlays that apply to it, and the scenes its references point
  into. It hands out elements and keeps what overlays answered (once per scene and element;
  an overlay asking for itself about the same element is an error, not a loop).

Transformations (``nolli/transformations/``) take a scene and return one; formats
(``nolli/formats/``) take a scene.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from functools import cached_property
from typing import Any, Protocol

from . import EdgeKey, Key, Model


class Overlay(Protocol):
    name: str

    def compute(self, element: Element) -> dict | None: ...


class OverlayCycle(Exception):
    pass


class Element:
    """A node or an edge of a scene."""

    __slots__ = ("__dict__", "_scene", "key")

    def __init__(self, scene: Scene, key: Key) -> None:
        self._scene = scene
        self.key = key

    @property
    def model_key(self) -> str:
        return self._scene.model.key

    @property
    def kind(self) -> str:
        return self._scene.model.find_kind(self.key)

    @property
    def is_edge(self) -> bool:
        return isinstance(self.key, EdgeKey)

    @property
    def id(self) -> str:
        """The key as text: a node's address, an edge's ``kind|src|dst|subkind``."""
        return self.key.format() if isinstance(self.key, EdgeKey) else self.key

    @property
    def meta(self) -> dict:
        """What the element's model says about itself (a change's ``meta["change"]``, a picture's
        ``meta["projection"]``)."""
        return self._scene.model.meta

    def find_holder(self, key: Key, role: str = "source") -> Element | None:
        """The element of this scene that stands for ``key`` of its ``role`` source (each node
        of a source is held by one element), None if none does."""
        k = self._scene.find_holder(key, role)
        return None if k is None else self._scene.find_element(k)

    @property
    def scene(self) -> Scene:
        """The scene, for what an overlay computes once for all of its elements (``scene.memo``)."""
        return self._scene

    # -- what it stands for ----------------------------------------------------

    @cached_property
    def sources(self) -> dict[str, tuple[Element, ...]]:
        return {role: tuple(src.find_element(k) for k in self._scene.list_refs(role, self.key) if src.model.has(k))
                for role, src in self._scene.sources.items()}

    def find_same(self, *roles: str) -> Element | None:
        """The element with this key in the first of ``roles`` (default: head, base, source)
        that has one: a projection's box that is a node of its source, a change's node as one
        side has it."""
        for role in roles or ("head", "base", "source"):
            for s in self.sources.get(role, ()):
                if s.key == self.key:
                    return s
        return None

    # -- structure -----------------------------------------------------------

    @property
    def src(self) -> Element:
        return self._scene.find_element(self.key.src)  # type: ignore[union-attr]

    @property
    def dst(self) -> Element:
        return self._scene.find_element(self.key.dst)  # type: ignore[union-attr]

    @property
    def parent(self) -> Element | None:
        p = self._scene.model.find_parent(self.key)  # type: ignore[arg-type]
        return None if p is None else self._scene.find_element(p)

    @property
    def children(self) -> list[Element]:
        return [self._scene.find_element(c) for c in self._scene.model.list_children(self.key)]  # type: ignore[arg-type]

    @property
    def out_edges(self) -> list[Element]:
        return [self._scene.find_element(e) for e in self._scene.model.list_out(self.key)]  # type: ignore[arg-type]

    @property
    def in_edges(self) -> list[Element]:
        return [self._scene.find_element(e) for e in self._scene.model.list_in(self.key)]  # type: ignore[arg-type]

    def iter_ancestors(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    # -- what is known about it --------------------------------------------------

    def get(self, overlay: str) -> dict | None:
        return self._scene._compute(self, overlay)

    def __repr__(self) -> str:
        return f"Element({self.id!r} in {self.model_key!r})"


class Scene:
    def __init__(self, model: Model, overlays: Iterable[Overlay], sources: Mapping[str, Scene] | None = None) -> None:
        self.model = model
        self.overlays: dict[str, Overlay] = {}
        for o in overlays:
            self.overlays[o.name] = o
        self.sources = dict(sources or {})
        self._elements: dict[Key, Element] = {}
        self._cache: dict[tuple[str, Key], dict | None] = {}
        self._busy: set[tuple[str, Key]] = set()
        self._memo: dict[Any, Any] = {}

    def with_overlays(self, *more: Overlay) -> Scene:
        """The same model and sources with more overlays (one of the same name replaces it)."""
        return Scene(self.model, [*self.overlays.values(), *more], self.sources)

    def has(self, key: Key) -> bool:
        return self.model.has(key)

    def find_element(self, key: Key) -> Element:
        el = self._elements.get(key)
        if el is None:
            if not self.model.has(key):
                raise KeyError(key)
            el = self._elements[key] = Element(self, key)
        return el

    def list_nodes(self) -> list[Element]:
        return [self.find_element(x) for x in sorted(self.model.kinds)]

    def list_edges(self, kinds: Iterable[str] | None = None) -> list[Element]:
        return [self.find_element(e) for e in self.model.list_edges(kinds)]

    def list_roots(self) -> list[Element]:
        return [self.find_element(x) for x in self.model.list_roots()]

    def list_refs(self, role: str, key: Key) -> tuple[Key, ...]:
        refs = self.model.refs.get(role)
        if refs is None:  # the same key on that side (a change's sides)
            return (key,) if role in self.model.refs else ()
        return refs.get(key, ())

    def find_holder(self, key: Key, role: str = "source") -> Key | None:
        """The key of the element that refers to ``key`` of its ``role`` source (the innermost,
        when several do)."""
        held = self._memo.get(("holders", role))
        if held is None:
            held = self._memo[("holders", role)] = {}
            for k, ks in (self.model.refs.get(role) or {}).items():
                for x in ks:
                    held[x] = k
        return held.get(key)

    def memo(self, fn: Callable[[Scene], Any]) -> Any:
        """``fn(scene)``, computed once per scene: for an overlay that needs all of a scene's
        elements at once (the numbered changes, where verdicts land)."""
        if fn not in self._memo:
            self._memo[fn] = fn(self)
        return self._memo[fn]

    def _compute(self, element: Element, overlay: str) -> dict | None:
        k = (overlay, element.key)
        if k in self._cache:
            return self._cache[k]
        if k in self._busy:
            raise OverlayCycle(f"{overlay} needs itself for {element.id}")
        o = self.overlays.get(overlay)
        if o is None:
            return None
        self._busy.add(k)
        try:
            value = o.compute(element)
        finally:
            self._busy.discard(k)
        self._cache[k] = value
        return value


# --- overlays every scene has ------------------------------------------------------------


class FixedOverlay:
    """Properties a transformation decided for the elements it made, as an extractor's
    are saved: ``projection`` (what is a container, which lines were lifted)."""

    def __init__(self, name: str, values: Mapping[Key, dict]) -> None:
        self.name, self.values = name, values

    def compute(self, element: Element) -> dict | None:
        return self.values.get(element.key)
