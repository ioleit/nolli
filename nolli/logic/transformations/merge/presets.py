"""The viewer's presets for a change: ``changes`` unfolds every box something changed in.

The changes preset is the review diagram's cut and what the review panel lists the changes of
(a table of its changed boxes and edges, nolli/logic/formats/table.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from nolli.logic.transformations import merge, projection
from nolli.logic.transformations.merge import reading
from nolli.logic.transformations.projection.reading import Reading
from nolli.model.scene import Scene
from nolli.model.schema import EntityId


def _find_changes_cut(scene: Scene, hidden: set[str]) -> set[str]:
    """Find the boxes to unfold so that each change outside ``hidden`` is drawn: the top of an
    added or removed subtree, an edited file, and both modules of a changed dependency between
    modules. Each is drawn as a box of its own, folded."""
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
    """The viewer's "all changes", and the review diagram's cut: for a change, every box that
    ``_find_changes_cut`` names is unfolded; for anything else, the default cut."""
    name = "changes"

    def find(self, g: Reading, hidden: set[str], st: projection.ProjectionSpec) -> set[str]:
        if not merge.is_change(g.scene):
            return projection.AtMost().find(g, hidden, st)
        return _find_changes_cut(g.scene, hidden)

    def name_for(self, g: Reading) -> str | None:
        return self.name if merge.is_change(g.scene) else projection.AtMost.name


PRESETS: dict[str, type[projection.Expansion]] = {**projection.PRESETS, "changes": Changes}  # every preset the viewer offers
