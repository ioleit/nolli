"""nolli as a library: what the command line (``nolli/cli.py``), the CI comment and the server
(``nolli/server/``) work with. ``Datasets`` is a store of models, where their commits come
from, and the overlays nolli shows on them; it opens a stored model as a scene (a commit, or
a change merged from its two commits), draws pictures of it, keeps and checks a review's
verdicts, and builds what is missing.

Below it: the model and its scenes (``nolli/model/``), the store (``nolli/store/``), where
commits come from (``nolli/sources/``), extraction (``nolli/extractors/``, run by
``nolli/build.py``), the transformations (``nolli/transformations/``), the review
(``nolli/review/``) and the formats (``nolli/formats/``), which take a scene.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from .build import Maps, Run, fetch_change, fetch_whole_model, is_fresh, run_here
from .extractors.overlays import make_base_overlays
from .model import Key, Model
from .model.keys import ChangeKey, Commit, ModelKey
from .model.scene import Overlay, Scene
from .progress import Progress
from .review.changes import find_review_cut
from .review.verdicts import check_verdicts, find_provenance, parse_verdicts, save_verdicts
from .sources import Sources
from .store import ModelStore
from .store.maps import MapStore
from .transformations.merge import is_change, merge
from .transformations.projection import SceneState, project

class Datasets:
    def __init__(self, store: ModelStore, sources: Sources | None = None) -> None:
        self.store, self.sources = store, sources

    # -- what is kept ------------------------------------------------------------

    def find_key(self, name: str) -> ModelKey | None:
        return self.store.find_key(name)

    def list_names(self) -> list[str]:
        return self.store.list_names()

    def open_scene(self, key: ModelKey) -> Scene | None:
        """The scene of a stored model: a change merged from its commits, or one commit. Raises
        NotAllowed as the store does."""
        return open_change_scene(self.store, key) if isinstance(key, ChangeKey) else open_commit_scene(self.store, key)

    def is_built(self, key: ModelKey) -> bool:
        """Kept, and built by this version of nolli."""
        return is_fresh(self.store.load_change(key) if isinstance(key, ChangeKey) else self.store.load_model(key))

    def build(self, key: ModelKey, *, about: dict | None = None, run: Run = run_here, progress: Progress | None = None,
              rebuild: bool = False) -> None:
        """Build what ``key`` names from ``sources`` unless a fresh one is kept. ``about``: what a
        change says about itself (``pr``, ``title``, ``description``, ``budget``)."""
        if self.sources is None:
            raise ValueError("no sources to build from")
        if isinstance(key, ChangeKey):
            fetch_change(self.store, self.sources, key, about=about, run=run, progress=progress, rebuild=rebuild)
        else:
            fetch_whole_model(self.store, self.sources, key, run=run, progress=progress, rebuild=rebuild)

    # -- pictures ------------------------------------------------------------------

    def draw(self, scene: Scene, state: SceneState) -> Scene:
        """The picture ``state`` asks for; a change's "changes" preset unfolds what it changed."""
        if state.preset == "changes" and state.focus is None and is_change(scene):
            state.focus = find_review_cut(scene)
        return project(scene, state)

    # -- a review's verdicts ---------------------------------------------------------

    def load_verdicts(self, scene: Scene) -> list[dict]:
        """A change's kept verdicts, in their order."""
        return parse_verdicts(self.store.open_maps("verdicts").load(scene.model.key))

    def find_provenance(self, key: ModelKey) -> dict | None:
        """Who made a change's verdicts; None without verdicts."""
        return find_provenance(self.store, key)

    def check_verdicts(self, scene: Scene, text: str) -> tuple[list[dict], list[str]]:
        """verdicts.json checked against the change's review diagram: (verdicts read, problems)."""
        from .review.brief import make_review_diagram

        return check_verdicts(scene, make_review_diagram(scene), text)

    def keep_verdicts(self, key: ModelKey, verdicts: list[dict], by: dict) -> None:
        """Make ``verdicts`` (read, with their ``ends``) the change's, with where they came from."""
        save_verdicts(self.store, key, verdicts, by)

    def recheck_verdicts(self, scene: Scene) -> tuple[list[dict] | None, list[str]]:
        """The kept verdicts checked again against the review diagram (None without verdicts)."""
        kept = self.load_verdicts(scene)
        return self.check_verdicts(scene, json.dumps(kept)) if kept else (None, [])


# -- scenes of stored or just built models ----------------------------------------------


def make_overlays(maps: MapStore) -> list[Overlay]:
    """The overlays nolli shows on any scene, on top of the extractors'."""
    from .transformations.merge import Change
    from .formats.descriptions.hover import Hover
    from .formats.descriptions.label import Label
    from .review.card import Review
    from .review.verdicts import Verdicts

    return [Change(maps.open_maps("content")), Verdicts(maps.open_maps("verdicts")), Label(), Hover(), Review()]


def open_scene(model: Model, maps: MapStore, overlays: list[Overlay] | None = None) -> Scene:
    """An extracted model with its extractors' overlays and nolli's (or ``overlays``)."""
    return Scene(model, [*make_base_overlays(model, maps), *(make_overlays(maps) if overlays is None else overlays)])


def open_commit_scene(store: ModelStore, commit: Commit, overlays: list[Overlay] | None = None) -> Scene | None:
    m = store.load_model(commit)
    return None if m is None else open_scene(m, store, overlays)


def open_change_scene(store: ModelStore, change: ChangeKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """The change merged from its two stored commits, with what it says about itself in its
    model's ``meta["change"]``."""
    about = store.load_change(change)
    if about is None:
        return None
    base, head = (open_commit_scene(store, c, overlays) for c in change.list_sides())
    if base is None or head is None:
        return None
    meta = {"repo": head.model.meta.get("repo"), "commit": change.head[:7], "files": head.model.meta.get("files"),
            "languages": {**base.model.meta.get("languages", {}), **head.model.meta.get("languages", {})},
            "change": dict(about)}
    return merge(base, head, change.format_key(), meta)


def open_built(model: Model, maps: Maps, overlays: list[Overlay] | None = None) -> Scene:
    """A model just extracted, not kept anywhere, as a scene."""
    from .store.maps import MemoryMaps

    store = MemoryMaps({n: {model.key: v} for n, v in maps.items()})
    return open_scene(model, store, overlays)


def merge_built(base: tuple[Model, Maps], head: tuple[Model, Maps], key: str, about: dict, content: Mapping[Key, dict],
                overlays: list[Overlay] | None = None) -> Scene:
    """A change of two models just extracted, not kept anywhere (``nolli diff``, tests)."""
    from .store.maps import MemoryMaps

    store = MemoryMaps()
    for m, maps in (base, head):
        for n, v in maps.items():
            store.open_maps(n).save(m.key, v)
    store.open_maps("content").save(key, content)
    bs, hs = (open_scene(m, store, overlays) for m, _ in (base, head))
    meta = {"repo": hs.model.meta.get("repo"), "commit": about.get("head", "")[:7], "files": hs.model.meta.get("files"),
            "languages": {**bs.model.meta.get("languages", {}), **hs.model.meta.get("languages", {})}, "change": dict(about)}
    return merge(bs, hs, key, meta)
