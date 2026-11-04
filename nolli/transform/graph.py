"""A scene read the way the projection reads it: kinds, names and attributes of nodes (the
``facts`` overlay), the edges with their facts, and for a change what the ``change`` overlay
says. The projection's algorithms (``rollup.py``, ``unfold.py``, ``project.py``) work on this,
so they read overlays in one place."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterator

from ..model import CONTAIN, EdgeKey
from ..scene import Scene

CONF_RANK = {"exact": 0, "heuristic": 1, "statistical": 2}


@dataclass
class Rel:
    """An edge of the scene with what its facts say together."""

    key: EdgeKey
    count: int
    confidence: str
    facts: list[dict] = field(default_factory=list)
    diff: dict | None = None  # in a change: {"status", "base", "head"}; None when unchanged

    @property
    def kind(self) -> str:
        return self.key.kind

    @property
    def src(self) -> str:
        return self.key.src

    @property
    def dst(self) -> str:
        return self.key.dst

    @property
    def subkind(self) -> str:
        return self.key.subkind


class Graph:
    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.model = scene.model
        self.is_change = "base" in scene.sources
        self._attrs: dict[str, dict] = {}

    # -- nodes -------------------------------------------------------------------

    def has(self, x: str) -> bool:
        return x in self.model.kinds

    def kind(self, x: str) -> str:
        return self.model.kinds[x]

    def attrs(self, x: str) -> dict:
        a = self._attrs.get(x)
        if a is None:
            a = self._attrs[x] = self.scene.find_element(x).get("facts") or {}
        return a

    def name(self, x: str) -> str:
        return self.attrs(x).get("name", x)

    def parent(self, x: str) -> str | None:
        return self.model.find_parent(x)

    def children(self, x: str) -> list[str]:
        return self.model.list_children(x)

    def roots(self) -> list[str]:
        return self.model.list_roots()

    def ancestors(self, x: str) -> Iterator[str]:
        return self.model.iter_ancestors(x)

    def descendants(self, x: str) -> Iterator[str]:
        return self.model.iter_descendants(x)

    @property
    def nodes(self) -> dict[str, str]:
        return self.model.kinds

    def diff(self, x: str) -> dict:
        """What the change did to a node (the ``change`` overlay); {} outside a change."""
        if not self.is_change:
            return {}
        return self.scene.find_element(x).get("change") or {}

    # -- edges -------------------------------------------------------------------

    @cached_property
    def relations(self) -> list[Rel]:
        out = []
        for e in self.model.edges:
            if e.kind == CONTAIN:
                continue
            el = self.scene.find_element(e)
            f = el.get("facts") or {}
            facts = f.get("facts") or []
            conf = max((x.get("confidence", "exact") for x in facts), key=lambda c: CONF_RANK.get(c, 0), default="exact")
            d = el.get("change") if self.is_change else None
            out.append(Rel(e, f.get("count", 0), conf, facts, d if d and d.get("status") in ("added", "removed", "changed") else None))
        return out
