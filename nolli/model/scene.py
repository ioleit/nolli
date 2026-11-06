"""Scenes join a model with the overlays that say what is known about it.

Anything known about a node or an edge besides its place in the structure comes from an
overlay; this module holds how overlays are asked, not what any one overlay knows.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterable, Mapping
from functools import cached_property
from typing import Protocol

from nolli.model import EdgeKey, Key, Model


def _hash_source(cls: type) -> str:
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return cls.__qualname__
    return hashlib.sha256(source.encode()).hexdigest()[:12]


class Overlay:
    """Code that says something about one element: ``compute(element)`` returns its properties
    (a dict) or None. The ``files`` overlay, for one, says a file's role and size.
    ``compute_all(scene)`` says it about every element of a scene at once, for the code that
    reads a whole model (the fold, the descriptions, the review); by default by asking
    ``compute`` about each, an overlay that has everything at hand (a stored map) answers
    with that.

    An overlay decides for itself where its knowledge comes from: maps kept in a store
    (``StoredMaps``, by model key: what extraction said, a review's verdicts), other overlays
    (``element.get``), or what the element stands for in the models it was made from
    (``element.sources``).

    What it says about a model can be kept in a store and read back instead of computed
    (``nolli/store/precomputed.py``); ``cache_version`` says which kept values are still its
    own."""

    name: str
    cache_version: str = ""  # which precomputed values of this overlay are acceptable; set once per class, below

    def __init_subclass__(cls, **kwargs) -> None:
        """``cache_version``, computed once when the class is made: values kept under another
        version are not read. By default a hash of the class's source, so a change to the code
        retires what it computed before; an overlay whose answers depend on more than its code
        (its arguments, a helper it calls) sets its own in its body, ``cache_version = "2"``."""
        super().__init_subclass__(**kwargs)
        if "cache_version" not in cls.__dict__:
            cls.cache_version = _hash_source(cls)

    def compute(self, element: Element) -> dict | None:
        raise NotImplementedError

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """What it says about every node and edge of the scene's model, by key; those it says
        nothing about are left out."""
        out: dict[Key, dict] = {}
        for key in [*scene.model.kinds, *scene.model.edges]:
            v = scene.find_element(key).get(self.name)
            if v is not None:
                out[key] = v
        return out


class StoredMaps(Protocol):
    """What an overlay reads kept maps from: the maps of one name in a store, by model key
    (``nolli/store/``, ``MapStore.open_maps(name)``), and nothing else of the store. An
    overlay only reads; the store writes (``nolli/store/precomputed.py``)."""

    def load(self, model_key: str) -> Mapping[Key, dict]: ...

    def load_about(self, model_key: str) -> dict | None: ...


class OverlayCycle(Exception):
    pass


class Element:
    """A node or an edge of a scene, as an overlay sees it: its key and kind, and for an edge
    its two ends; its place in the structure; what it stands for (``sources``); and
    ``get(overlay)``, any overlay's properties of it."""

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
        """The scene, for an overlay that answers for all of its elements at once (``scene.read_all``)."""
        return self._scene

    # -- what it stands for ----------------------------------------------------

    @cached_property
    def sources(self) -> dict[str, tuple[Element, ...]]:
        """What it stands for, by role: the elements of the source scenes (``source`` for a
        projection, ``base`` and ``head`` for a change)."""
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
        """The properties ``overlay`` gives this element, None if it gives none or the scene
        has no such overlay."""
        return self._scene._compute(self, overlay)

    def __repr__(self) -> str:
        return f"Element({self.id!r} in {self.model_key!r})"


class Scene:
    """A model, the overlays that apply to it, and the scenes its references point into
    (``sources``, by role).

    It hands out elements. An element asks an overlay through it (``get``); once an overlay
    has answered for the whole model (``read_all``), the scene keeps that and answers elements
    from it. An overlay asking for itself about the same element raises ``OverlayCycle``
    rather than loop."""

    def __init__(self, model: Model, overlays: Iterable[Overlay], sources: Mapping[str, Scene] | None = None) -> None:
        self.model = model
        self.overlays: dict[str, Overlay] = {}
        for o in overlays:
            self.overlays[o.name] = o
        self.sources = dict(sources or {})
        self._elements: dict[Key, Element] = {}
        self._whole: dict[str, Mapping[Key, dict]] = {}  # what an overlay said about the whole model, by overlay name
        self._holders: dict[str, dict[Key, Key]] = {}  # by role: a source's key -> the element here that refers to it
        self._busy: set[tuple[str, Key]] = set()  # (overlay, key) being computed, to catch a cycle

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
        held = self._holders.get(role)
        if held is None:
            held = self._holders[role] = {}
            for k, ks in (self.model.refs.get(role) or {}).items():
                for x in ks:
                    held[x] = k
        return held.get(key)

    def read_all(self, overlay: str) -> Mapping[Key, dict]:
        """Everything ``overlay`` says about this scene's elements, by key ({} if the scene has
        no such overlay). Computed once per scene; from then on elements are answered from it."""
        got = self._whole.get(overlay)
        if got is None:
            o = self.overlays.get(overlay)
            got = self._whole[overlay] = {} if o is None else o.compute_all(self)
        return got

    def _compute(self, element: Element, overlay: str) -> dict | None:
        whole = self._whole.get(overlay)
        if whole is not None:
            return whole.get(element.key)
        o = self.overlays.get(overlay)
        if o is None:
            return None
        k = (overlay, element.key)
        if k in self._busy:
            raise OverlayCycle(f"{overlay} needs itself for {element.id}")
        self._busy.add(k)
        try:
            return o.compute(element)
        finally:
            self._busy.discard(k)


# --- overlays every scene has ------------------------------------------------------------


class FixedOverlay(Overlay):
    """Properties a transformation decided for the elements it made, as an extractor's
    are saved: ``projection`` (what is a container, which lines were lifted)."""

    def __init__(self, name: str, values: Mapping[Key, dict], cache_version: str = "") -> None:
        self.name, self.values, self._cache_version = name, values, cache_version

    @property
    def cache_version(self) -> str:
        """As given (what made the values says which they are: an extractor's version); else
        the values are kept under no version and read back whatever it is."""
        return self._cache_version

    def compute(self, element: Element) -> dict | None:
        return self.values.get(element.key)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        return self.values
