"""Every path from nolli/operations.py to nolli/store/, as Mermaid: python dev/examples/paths_between.py <nolli checkout>

An example of nolli as a library: a scene of the files as they are, folded to module level by a
predicate, a custom transformation that keeps the boxes on a path, and the result as Mermaid."""

import sys
from pathlib import Path

from nolli import operations
from nolli.logic import transformations
from nolli.logic.formats import mermaid
from nolli.logic.transformations import projection
from nolli.logic.transformations.select import select
from nolli.model.scene import Scene
from nolli.store import MemoryStore

repo = Path(sys.argv[1])
files = MemoryStore()
scene = operations.open_scene(files, operations.build_files(files, repo))                     # the files as they are, with nolli's overlays


spec = projection.ProjectionSpec(expand=projection.Dirs("nolli"), groups=[], utilities=0)   # module level under nolli/, no classes
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


def on_paths(folded: Scene) -> Scene:                               # the custom transformation
    """Keep only the boxes on a path from operations.py to store/, and the boxes they are drawn in."""
    m = folded.model
    down = reach(["repo:nolli/operations.py"], lambda x: [e.dst for e in m.list_out(x)])
    up = reach([x for x in m.entities if x.startswith("repo:nolli/store")], lambda x: [e.src for e in m.list_in(x)])
    keep = down & up
    return select(folded, keep | {p for x in keep for p in m.iter_ancestors(x)})


print(mermaid.render(transformations.apply(folded, on_paths)))
