"""nolli with its four pipeline packages under one proposed parent, logic/, as Mermaid:
python dev/examples/logic.py <nolli checkout>

An example of nolli as a library: a scene of the files as they are, a custom transformation
that proposes a directory that is not there and moves four packages into it, the proposal
folded to package level like any scene, cut down to nolli/ itself, and the result as Mermaid."""

import sys
from pathlib import Path

from nolli import build, scenes, transformations
from nolli.formats import mermaid
from nolli.model import EdgeKey, Model, address, metamodel
from nolli.model.scene import Scene
from nolli.transformations import projection

NOLLI = address.repo("nolli")
LOGIC = address.repo("nolli/logic")                                 # the proposed parent: no such directory exists
MEMBERS = [address.repo(f"nolli/{p}") for p in ("transformations", "formats", "sources", "extractors")]

repo = Path(sys.argv[1])
scene = scenes.open_built(*build.extract(repo))                     # the files as they are, with nolli's overlays


def group_into_logic(scene: Scene) -> Model:                        # the custom transformation
    """The same model with one more directory, logic/ under nolli/, holding the four packages
    in place of nolli/ itself. Every node and edge keeps its address, so it is its namesake's
    in the source and what extraction said about it holds; logic/ is new and nothing is known
    about it but its place."""
    m = scene.model
    kinds = {**m.kinds, LOGIC: "directory"}
    moved = {EdgeKey(metamodel.CONTAIN, NOLLI, x) for x in MEMBERS}
    edges = [e for e in m.edges if e not in moved]
    edges.append(EdgeKey(metamodel.CONTAIN, NOLLI, LOGIC))
    edges.extend(EdgeKey(metamodel.CONTAIN, LOGIC, x) for x in MEMBERS)
    return Model(f"{m.key}|logic", kinds, edges, refs={"source": None})   # checked against the metamodel


proposal = transformations.apply(scene, group_into_logic)

# package level: nolli/ unfolded, logic/ one box among its packages
spec = projection.ProjectionSpec.expanded_if(lambda el: el.key in (address.REPO_ROOT, NOLLI), groups=[], utilities=0)
folded = transformations.apply(proposal, projection.fold(spec))


def inside_nolli(folded: Scene) -> Model:
    """nolli/ and what is drawn in it, nothing else of the repository."""
    return folded.model.select({NOLLI, *folded.model.iter_descendants(NOLLI)})


print(mermaid.render(transformations.apply(folded, inside_nolli)))
