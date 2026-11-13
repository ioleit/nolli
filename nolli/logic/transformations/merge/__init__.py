"""Merges the models of two commits into the model of their change.

It also says what the change did as a whole: its totals and its links on GitHub. ``change``
holds the ``change`` overlay, which says what it did to each node and edge; ``reading`` reads a
change's scene for it, and ``presets`` holds the viewer's preset that unfolds every change.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

from nolli.model import vocabulary
from nolli.model.keys import ModelKey
from nolli.model.model import Key, Model
from nolli.model.scene import Element, Overlay, Scene
from nolli.model.schema import RelationKind


def is_change(scene: Scene) -> bool:
    return "base" in scene.sources


# ---------- links ----------

class Links:
    """GitHub URLs for a change; every method returns None when the remote is unknown."""

    def __init__(self, change: dict) -> None:
        """``change``: what the change says about itself (``meta["change"]``)."""
        d = change or {}
        self.remote: str | None = d.get("remote")
        self.base: str | None = d.get("base")
        self.head: str | None = d.get("head")
        self.pr: int | None = d.get("pr")

    @staticmethod
    def anchor(path: str) -> str:
        """GitHub's anchor for a file in a diff view: sha256 of its path."""
        return "diff-" + hashlib.sha256(path.encode()).hexdigest()

    def change(self) -> str | None:
        """Return the URL of the PR, or of the compare view of the two commits."""
        if not self.remote:
            return None
        return f"{self.remote}/pull/{self.pr}" if self.pr else f"{self.remote}/compare/{self.base}...{self.head}"

    def diff(self, path: str, line: int | None = None, side: str = "head") -> str | None:
        """Return the URL of a file, and optionally a line on one side, in the PR's files view or
        the compare view."""
        if not self.remote:
            return None
        page = f"{self.remote}/pull/{self.pr}/files" if self.pr else f"{self.remote}/compare/{self.base}...{self.head}"
        return f"{page}#{self.anchor(path)}" + (f"{'R' if side == 'head' else 'L'}{line}" if line else "")

    def blob(self, path: str, side: str = "head", start: int | None = None, end: int | None = None) -> str | None:
        """Return the URL of a file at one commit, optionally pointing at a line range."""
        if not self.remote:
            return None
        sha = self.head if side == "head" else self.base
        frag = f"#L{start}" + (f"-L{end}" if end and end != start else "") if start else ""
        return f"{self.remote}/blob/{sha}/{path}{frag}"


# ---------- the change in numbers ----------

def describe_change(about: dict, content: Mapping[Key, dict]) -> dict:
    """Build the ``meta["change"]`` of a change's model, which scenes drawn from it carry along.
    It holds what the change says about itself (title, pr, description, base, head, remote,
    …), git's totals of files changed and lines added and removed, and a link to it."""
    d = dict(about)
    d.update({"base_short": (d.get("base") or "")[:7], "head_short": (d.get("head") or "")[:7], "files_changed": len(content),
              "added": sum(c.get("added", 0) for c in content.values()), "removed": sum(c.get("removed", 0) for c in content.values())})
    d["link"] = Links(d).change()
    return d


class SidesSaid(Overlay):
    """What extraction said on the sides, as the change's own: for each node and edge, head's
    word, else base's, one per shape (``vocabulary.SHAPES``). Same address, same thing, so a
    map kept for a commit holds for the change's namesake of its node; read from the sides
    when first asked, kept nowhere."""

    def __init__(self, name: str) -> None:
        self.name = name

    def compute(self, element: Element) -> dict | None:
        return element.scene.read_all(self.name).get(element.key)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        model = scene.model
        out: dict[Key, dict] = {}
        for role in ("base", "head"):  # head's word last: it replaces base's
            for k, v in scene.sources[role].read_all(self.name).items():
                if model.has(k):
                    out[k] = v
        return out


def merge_models(key: ModelKey, base: Model, head: Model, meta: dict | None = None) -> Model:
    """Build the model holding every node and edge of either side, each standing for its namesake
    on each side (``union`` says so). Same address, same thing: a node or edge is the same on both sides
    when its address is; a node owned on both sides is owned as head has it."""
    entities = {**base.entities, **head.entities}
    owned = {e.dst for e in head.edges if e.kind == RelationKind.CONTAIN}
    edges = list(head.edges) + [e for e in base.edges if not (e.kind == RelationKind.CONTAIN and e.dst in owned)]
    return Model(key, entities.values(), edges, meta=dict(meta or {}), check=False)


def union(key: ModelKey, meta: dict | None = None, kept: Model | None = None) -> Callable[[Mapping[str, Scene]], Scene]:
    """Merge as a transformation: of two scenes by role (``base``, ``head``), the scene of the
    merged model (``merge_models``, or ``kept``: the same as it was kept at diff time, with
    ``meta`` on top), each element standing for its namesake on each side, and what
    extraction said about it on the sides as the change's (``SidesSaid``, one per shape)."""
    def make(sides: Mapping[str, Scene]) -> Scene:
        if kept is None:
            model = merge_models(key, sides["base"].model, sides["head"].model, meta)
        else:
            model = kept
            model.meta = {**model.meta, **(meta or {})}
        return Scene(model, [SidesSaid(n) for n in vocabulary.SHAPES], sides, dict.fromkeys(sides))

    return make
