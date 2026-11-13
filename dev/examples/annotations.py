"""Annotations, given as data and kept as an overlay on a projection:
python dev/examples/annotations.py <nolli checkout> <annotations.json> [<data directory>]

An example of nolli as a library. Where custom_overlay.py defines an overlay in code, this one
is handed its values: a JSON object from element ids to what is said about each. It opens a
scene of the files as they are, projects it to nolli's top-level modules and keeps the
annotations as the projection's ``annotations`` map. Some annotate edges that only the
projection has (lines between folded boxes), so they belong to the projected model, under its
key: the key of the model it was projected from plus which projection it is.

Element ids are the model's own: an entity's address (an ``EntityId``, ``repo:nolli/model``)
or a relation's ``kind|src|dst|subkind`` (a ``RelationId``,
``use|repo:nolli/server|repo:nolli/logic|import``). Both are text, so the JSON's keys are the
store's keys as they are.

Everything is kept in a ``RepoDirectoryStore`` under the data directory (a new temporary one
unless one is given, printed), so the files can be looked at afterwards.
The model is keyed as ``nolli extract`` keys a working tree, ``<path>@worktree``, so the
projection's key has a repository and the repository's store can keep it."""

import json
import sys
import tempfile
from pathlib import Path

from nolli import operations
from nolli.logic import transformations
from nolli.logic.transformations import projection
from nolli.model.keys import Repo
from nolli.store import RepoStoreProvider, precomputed

repo, given = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
data = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp(prefix="nolli-annotations-"))
print("data directory:", data)
stores = RepoStoreProvider(data)                                    # decides once per repository whether it may be read
source = Repo(str(repo)).make_commit("worktree")                    # the files as they are, as `nolli extract` keys them
store = stores.open(source.repo)                                    # the model and every map kept for it, this repository only
scene = operations.open_scene(store, operations.build_files(store, repo, source))

# nolli/ unfolded, its directories folded: lines between them stand for many edges of the scene
modules = projection.ProjectionSpec(expand=projection.Ids(("repo:", "repo:nolli")), groups=[], utilities=0)

key = projection.make_key(scene.model.key, modules)                 # a ProjectionKey, known before projecting
print("projected model:", key.format_key())                         # <path>@worktree~project:x=repo:,repo:nolli&g=&nu=1
print("projected from: ", key.source.format_key())                  # the source's key, read back
print("projected as:   ", projection.ProjectionSpec.parse(key.spec).format())   # the spec, read back

projected = transformations.apply(scene, projection.fold(modules))
assert projected.model.key == key                                   # the projection has the key made for it

# -- keep the annotations as a map of the projected model ----------------------------------

values = json.loads(given.read_text())                             # element id -> what is said about it

# Sanity check that the specified annnotation actually refer to something in the model
missing = [k for k in values if not projected.model.has(k)]
assert not missing, f"not in the projected model: {missing}"


store.open_maps("annotations").save(key.format_key(), values, about={"source": given.name})
# keys are ids (EntityId | RelationId, both str); save adds when to about ("at")
print("maps kept:", store.list_map_names(key.format_key()), "about:", store.open_maps("annotations").load_about(key.format_key()))

# -- read them back on the same projection, made afresh ------------------------------------

again = transformations.apply(scene, projection.fold(modules))
assert again.model.key == key                                       # the same projection of the same model: the same key
read = again.with_overlays(precomputed.strictly_precomputed_overlay(store, "annotations"))
for k, v in read.read_all("annotations").items():
    el = read.find_element(k)
    only_here = not scene.model.has(k)                              # an element only the projection has, such as a line between folded boxes
    print(f"{el.id}: {v['text']}" + ("   (only in the projection)" if only_here else ""))   # el.id is the key, as in the JSON
print("the annotations were kept on the projected model and read back")
