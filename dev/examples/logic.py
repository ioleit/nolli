"""nolli with its four pipeline packages under one proposed parent, logic/, as Mermaid:
python dev/examples/logic.py <nolli checkout>

An example of nolli as a library: a scene of the files as they are, a custom transformation
that proposes a directory that is not there and moves four packages into it, the proposal
folded to package level like any scene, cut down to nolli/ itself, and the result as Mermaid."""

import sys
from pathlib import Path

from nolli import build, scenes, transformations
from nolli.formats import mermaid
from nolli.model.keys import StringKey
from nolli.model.model import Entity, Model, Relation
from nolli.model.scene import Scene
from nolli.model.schema import EntityId, RelationKind
from nolli.transformations import projection
from nolli.transformations.select import select

NOLLI = EntityId.from_path("nolli")
LOGIC = EntityId.from_path("nolli/logic")                                 # the proposed parent: no such directory exists
MEMBERS = [EntityId.from_path(f"nolli/{p}") for p in ("transformations", "formats", "sources", "extractors")]

repo = Path(sys.argv[1])
scene = scenes.open_built(*build.extract(repo))                     # the files as they are, with nolli's overlays


def group_into_logic(scene: Scene) -> Scene:                        # the custom transformation
    """The same model with one more directory, logic/ under nolli/, holding the four packages
    in place of nolli/ itself. Every node and edge keeps its address, so it is its namesake's
    in the source and what extraction said about it holds; logic/ is new and nothing is known
    about it but its place."""
    m = scene.model
    entities = [*m.entities.values(), Entity(LOGIC, "directory")]
    moved = {Relation(RelationKind.CONTAIN, NOLLI, x) for x in MEMBERS}
    edges = [e for e in m.edges if e not in moved]
    edges.append(Relation(RelationKind.CONTAIN, NOLLI, LOGIC))
    edges.extend(Relation(RelationKind.CONTAIN, LOGIC, x) for x in MEMBERS)
    model = Model(StringKey(f"{m.key.format_key()}|logic"), entities, edges)   # each entity and relation checked
    return Scene(model, [], {"source": scene}, {"source": None})     # each element its namesake in the source


proposal = transformations.apply(scene, group_into_logic)

# package level: nolli/ unfolded, logic/ one box among its packages
spec = projection.ProjectionSpec.expanded_if(lambda el: el.key in (EntityId.REPO_ROOT, NOLLI), groups=[], utilities=0)
folded = transformations.apply(proposal, projection.fold(spec))


def inside_nolli(folded: Scene) -> Scene:
    """nolli/ and what is drawn in it, nothing else of the repository."""
    return select(folded, {NOLLI, *folded.model.iter_descendants(NOLLI)})


print(mermaid.render(transformations.apply(folded, inside_nolli)))
