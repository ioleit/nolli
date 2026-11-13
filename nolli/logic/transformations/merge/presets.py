"""Adds the viewer's ``changes`` preset, which unfolds every node something changed in.

The review diagram is drawn with it, and the review panel lists the changed nodes and edges it
draws, from a table of it (nolli/logic/formats/table.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from nolli.logic.transformations import merge, projection
from nolli.logic.transformations.merge import reading
from nolli.logic.transformations.projection.reading import Reading
from nolli.model.scene import Scene
from nolli.model.schema import EntityId


def _find_changes_cut(scene: Scene, hidden: set[str]) -> set[str]:
    """Find the nodes to unfold so that every change outside ``hidden`` is drawn as a node or edge
    of its own. The changes are the top node of each added or removed subtree, each edited file,
    and the two modules of each changed dependency between modules; each is drawn folded."""
    g = reading.Reader(scene)
    shown = lambda x: x not in hidden and not any(a in hidden for a in g.ancestors(x))
    out = {EntityId.REPO_ROOT}
    for x in g.nodes:
        d, kind = g.diff(x), g.kind(x)
        st = d.get("status")
        if st in ("added", "removed") and kind not in ("group", "unresolved"):
            p = g.parent(x)
            top = p is None or g.diff(p).get("status") != st
        else:
            top = kind == "file" and "git" in d
        if top and shown(x):
            out.update(g.ancestors(x))
    for r in g.relations:
        if r.diff is None:
            continue
        a, b = reading.find_module(g, r.src), reading.find_module(g, r.dst)
        if a != b and shown(a) and shown(b):
            out.update(g.ancestors(a))
            out.update(g.ancestors(b))
    return out


@dataclass(frozen=True)
class Changes(projection.Expansion):
    """The viewer's "all changes" preset, with which the review diagram is drawn. On a change it
    unfolds the nodes ``_find_changes_cut`` finds; on anything else it is the default cut."""
    name = "changes"

    def find(self, g: Reading, hidden: set[str], st: projection.ProjectionSpec) -> set[str]:
        if not merge.is_change(g.scene):
            return projection.AtMost().find(g, hidden, st)
        return _find_changes_cut(g.scene, hidden)

    def name_for(self, g: Reading) -> str | None:
        return self.name if merge.is_change(g.scene) else projection.AtMost.name


PRESETS: dict[str, type[projection.Expansion]] = {**projection.PRESETS, "changes": Changes}  # every preset the viewer offers
