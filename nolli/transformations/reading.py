"""What the projection reads off a scene, and from where: the structure from the model, what a
file or directory is from the file tree (``read_file``), the references behind each edge from
the extractor that found it (``read_references``), and in a change what changed (the
``change`` overlay). Nothing else: the projection's algorithms (``rollup.py``, ``unfold.py``,
``projection.py``) take a ``Reading``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property
from urllib.parse import unquote

from nolli.model import CONTAIN, EdgeKey, address
from nolli.model.scene import Scene
from nolli.model.vocabulary import FileFacts, Reference, read_file, read_references

CONF_RANK = {"exact": 0, "heuristic": 1, "statistical": 2}


@dataclass
class Rel:
    """An edge with the references it stands for."""

    key: EdgeKey
    count: int
    confidence: str
    references: list[Reference] = field(default_factory=list)
    diff: dict | None = None  # in a change: {"status", "base", "head", …}; None when unchanged

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


class Reading:
    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.model = scene.model
        self.is_change = "base" in scene.sources
        self._files: dict[str, dict] = {}

    # -- structure ---------------------------------------------------------------

    def has(self, x: str) -> bool:
        return x in self.model.kinds

    def kind(self, x: str) -> str:
        return self.model.kinds[x]

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

    def segment(self, x: str) -> str:
        """The last step of a node's address: a file's or directory's name, a service's host."""
        if address.is_repo(x):
            return address.path_of(x).rsplit("/", 1)[-1] if address.path_of(x) != "." else ""
        return unquote(x.split(":", 1)[1].rsplit("/", 1)[-1])

    # -- what the file tree says ---------------------------------------------------

    def file(self, x: str) -> FileFacts | dict:
        f = self._files.get(x)
        if f is None:
            f = self._files[x] = read_file(self.scene.find_element(x))
        return f

    # -- what changed ------------------------------------------------------------

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
            refs = read_references(el)
            conf = max((r["confidence"] for r in refs), key=lambda c: CONF_RANK.get(c, 0), default="exact")
            d = el.get("change") if self.is_change else None
            out.append(Rel(e, sum(r["count"] for r in refs), conf, refs,
                           d if d and d.get("status") in ("added", "removed", "changed") else None))
        return out

    def find_unresolved_language(self, x: str) -> str:
        """The language an unresolved reference was written in, as its extractor says, else the
        sort of reference its address names (``http``)."""
        el = self.scene.find_element(x)
        for name in el.meta.get("overlays") or []:
            v = el.get(name)
            if v and v.get("language"):
                return v["language"]
        return x.split(":")[1]
