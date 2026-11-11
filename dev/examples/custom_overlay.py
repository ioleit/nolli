"""A custom overlay on every node and edge of a checkout, kept so it is not computed twice:
python dev/examples/custom_overlay.py <nolli checkout>

An example of nolli as a library. It opens a scene of the files as they are and defines an
overlay of its own, which says something about each element and handles folded boxes. The
overlay's values are kept in a store for the scene and for one projection of it, and the
kept overlay is then used instead, both on those and on a projection whose values were not
kept."""

import sys
from pathlib import Path

from nolli import operations
from nolli.logic import transformations
from nolli.logic.transformations import projection
from nolli.model.keys import StringKey
from nolli.model.scene import Element, Overlay, Scene
from nolli.store import MemoryStore
from nolli.store.maps import MemoryMaps

repo = Path(sys.argv[1])
files = MemoryStore()                                               # or a DirectoryStore
scene = operations.open_scene(files, operations.build_files(files, repo, StringKey("nolli@worktree")))   # the files as they are, with nolli's overlays


class Cool(Overlay):                                                # the custom overlay
    """The ``cool`` overlay: one word of praise for every node and edge, and for a folded box
    how much it stands for."""

    name = "cool"
    cache_version = "1"                                             # bumped when the answers change: older kept values are not read

    def compute(self, element: Element) -> dict | None:
        if element.is_edge:
            return {"remark": "cool edge, bro"}
        inside = [s for s in element.sources.get("source", ()) if s.key != element.key]
        if inside:
            return {"remark": f"cool folded node comprising {len(inside)} other elements, bro"}
        return {"remark": "cool node, bro"}


def show(title: str, *shown: Scene, box: str = "repo:nolli/model/scene.py") -> None:
    """One box and one edge of each scene, as the ``cool`` overlay sees them."""
    for s in shown:
        el = s.find_element(box)
        edge = (el.out_edges or el.in_edges)[0]
        print(f"{title}: {el.id}: {el.get('cool')['remark']} | {edge.id}: {edge.get('cool')['remark']}")


live = scene.with_overlays(Cool())                                  # computed when asked
show("live", live)
print(len(live.read_all("cool")), "elements have a remark")

# -- keep what it says, use the kept answers instead ---------------------------------------

store = MemoryMaps()                                                # or a DirectoryStore

# module level: every directory unfolded, nothing else (a common projection; a named preset may come later)
modules = projection.ProjectionSpec(expand=projection.Where(lambda el: el.key == "repo:" or el.kind == "directory"), groups=[], utilities=0)
# one directory unfolded, a projection whose values are not kept
one_dir = projection.ProjectionSpec(expand=projection.Where(lambda el: el.key in ("repo:", "repo:nolli", "repo:nolli/model")), groups=[], utilities=0)

store.save_scene(live)                                              # the model, and what every overlay says about it
store.precompute_overlay_values(transformations.apply(live, projection.fold(modules)), Cool())   # a projection, this overlay only
print("kept:", store.list_map_names(scene.model.key.format_key()), "for the scene;", [k for k in store.maps["cool"]], "for cool")

cool = store.caching_overlay(Cool())                                # Cool, answered from its kept values where they exist

kept = scene.with_overlays(cool)                                    # read from the store
kept_modules = transformations.apply(kept, projection.fold(modules))   # read from the store
kept_one_dir = transformations.apply(kept, projection.fold(one_dir))   # computed; what it asks of its sources is read
show("kept", kept, kept_modules, kept_one_dir)

for a, b in [(live, kept), (transformations.apply(live, projection.fold(modules)), kept_modules)]:
    assert a.read_all("cool") == b.read_all("cool")
print("live and kept agree on the scene and the module-level projection")
