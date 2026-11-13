"""nolli with the layers above its logic under one proposed parent, app/, as Mermaid:
python dev/examples/propose_package.py <nolli checkout>

An example of nolli as a library: a scene of the files as they are, a custom transformation
that proposes a directory that is not there and moves the command line and the
server into it, the proposal folded to package level like any scene, cut down to nolli/
itself, and the result as Mermaid. (nolli/logic/ was proposed the same way before it existed.)"""

import sys
from pathlib import Path

from nolli import operations
from nolli.logic import transformations
from nolli.logic.formats import mermaid
from nolli.logic.transformations import projection
from nolli.logic.transformations.select import select
from nolli.model.keys import Repo, StringKey
from nolli.model.model import Entity, Model, Relation
from nolli.model.scene import Scene
from nolli.model.schema import EntityId, RelationKind
from nolli.store import MemoryStore

NOLLI = EntityId.from_path("nolli")
APP = EntityId.from_path("nolli/app")                                     # the proposed parent: no such directory exists
MEMBERS = [EntityId.from_path(f"nolli/{p}") for p in ("cli.py", "server")]

repo = Path(sys.argv[1])
files = MemoryStore()
scene = operations.open_scene(files, operations.build_files(files, repo, Repo(str(repo.resolve())).make_commit("worktree")))                     # the files as they are, with nolli's overlays


def group_into_app(scene: Scene) -> Scene:                          # the custom transformation
    """Return the same model with one more directory, app/ under nolli/, that holds the three members
    in place of nolli/ itself. Every node and edge keeps its address, so it is its namesake's
    in the source and what extraction said about it holds; app/ is new and nothing is known
    about it but its place."""
    m = scene.model
    entities = [*m.entities.values(), Entity(APP, "directory")]
    moved = {Relation(RelationKind.CONTAIN, NOLLI, x) for x in MEMBERS}
    edges = [e for e in m.edges if e not in moved]
    edges.append(Relation(RelationKind.CONTAIN, NOLLI, APP))
    edges.extend(Relation(RelationKind.CONTAIN, APP, x) for x in MEMBERS)
    model = Model(StringKey("app", m.key.repo), entities, edges)      # a model of the same repository; each entity and relation checked
    return Scene(model, [], {"source": scene}, {"source": None})     # each element its namesake in the source


proposal = transformations.apply(scene, group_into_app)

# package level: nolli/ unfolded, app/ one box among its packages
spec = projection.ProjectionSpec(expand=projection.Ids((EntityId.REPO_ROOT, NOLLI)), groups=[], utilities=0)
folded = transformations.apply(proposal, projection.fold(spec))


def inside_nolli(folded: Scene) -> Scene:
    """nolli/ and what is drawn in it, nothing else of the repository."""
    return select(folded, {NOLLI, *folded.model.iter_descendants(NOLLI)})


print(mermaid.render(transformations.apply(folded, inside_nolli)))
