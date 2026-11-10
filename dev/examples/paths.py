"""Every path from nolli/datasets.py to nolli/store/, as Mermaid: python dev/examples/paths.py <nolli checkout>

An example of nolli as a library: a scene of the files as they are, folded to module level by a
predicate, a custom transformation that keeps the boxes on a path, and the result as Mermaid."""

import sys
from pathlib import Path

from nolli import build, scenes, transformations
from nolli.formats import mermaid
from nolli.model.model import Model
from nolli.model.scene import Element, Scene
from nolli.transformations import projection
from nolli.transformations.select import select

repo = Path(sys.argv[1])
scene = scenes.open_built(*build.extract(repo))                     # the files as they are, with nolli's overlays


def is_interesting_for_this_oneoff(el: Element) -> bool:
    """Unfold the repository and every directory under nolli/: module level, no classes."""
    return el.key == "repo:" or el.kind == "directory" and el.key.path.startswith("nolli")


spec = projection.ProjectionSpec.expanded_if(is_interesting_for_this_oneoff, groups=[], utilities=0)
folded = transformations.apply(scene, projection.fold(spec))


def reach(froms, step):
    """Everything reachable from ``froms``, one ``step`` at a time."""
    seen, todo = set(froms), list(froms)
    while todo:
        for y in step(todo.pop()):
            if y not in seen:
                seen.add(y)
                todo.append(y)
    return seen


def on_paths(folded: Scene) -> Model:                               # the custom transformation
    """The boxes on a path from datasets.py to store/, and the boxes they are drawn in."""
    m = folded.model
    down = reach(["repo:nolli/datasets.py"], lambda x: [e.dst for e in m.list_out(x)])
    up = reach([x for x in m.entities if x.startswith("repo:nolli/store")], lambda x: [e.src for e in m.list_in(x)])
    keep = down & up
    return select(m, keep | {p for x in keep for p in m.iter_ancestors(x)})


print(mermaid.render(transformations.apply(folded, on_paths)))
