"""Opens models as scenes, with what extraction said and nolli's overlays.

A model may be one just extracted and kept nowhere, or one kept in a store: a commit, or a
change merged from its two commits.
"""

from __future__ import annotations

from collections.abc import Mapping

from nolli.logic import transformations
from nolli.logic.transformations import merge
from nolli.model import vocabulary
from nolli.model.keys import ChangeKey, CommitKey, ModelKey
from nolli.model.model import Key, Model
from nolli.model.scene import Overlay, Scene
from nolli.store import ModelStore
from nolli.store.maps import MapStore


def _make_derived_overlays() -> list[Overlay]:
    """Make the overlays nolli derives from any model's facts and keeps with it at build time
    (``precompute``): what its nodes are called (``names``)."""
    from nolli.logic.formats.descriptions.names import Names

    return [Names()]


def _make_kept_overlays(maps: MapStore, key: ModelKey) -> list[Overlay]:
    """Make the overlays a stored model keeps the values of (``precompute``) and answers from
    when it is opened: what nolli derives from any model's facts, and for a change what it says
    about itself."""
    return [*_make_derived_overlays(), *(_make_change_overlays(maps) if isinstance(key, ChangeKey) else [])]


def _make_change_overlays(maps: MapStore) -> list[Overlay]:
    """Make the overlays that compute what a change says about itself from its sides: what changed
    (``change``) and which numbered changes each node and edge is about (``review``). Their values
    are kept with the change at diff time (``precompute``) and read back from there
    (``_open_change_scene``)."""
    from nolli.logic.transformations.merge import Change
    from nolli.review.card import Review

    return [Change(maps.open_maps("content")), Review()]


def describe_change_meta(base: Model, head: Model, about: dict) -> dict:
    """Build the ``meta`` of a change's merged model from its sides: the repository, the head
    commit, its files and languages. What the change says about itself (``about``, kept as
    change.json) is not included; it is added as ``meta["change"]`` by ``merge.describe_change``
    when the scene is opened, so that it is read from one place."""
    return {"repo": head.meta.get("repo"), "commit": about.get("head", "")[:7], "files": head.meta.get("files"),
            "languages": {**base.meta.get("languages", {}), **head.meta.get("languages", {})}}


def _describe_scene_meta(base: Model, head: Model, about: dict, content: Mapping[Key, dict]) -> dict:
    return {**describe_change_meta(base, head, about), "change": merge.describe_change(about, content)}


def _make_overlays(maps: MapStore) -> list[Overlay]:
    """Make the overlays nolli shows on any scene, on top of what extraction said."""
    from nolli.logic.formats.descriptions.hover import Hover
    from nolli.logic.formats.descriptions.label import Label
    from nolli.logic.transformations.merge import Change
    from nolli.review.card import Review
    from nolli.review.verdicts import Verdicts

    derived = [maps.caching_overlay(o) for o in _make_derived_overlays()]
    return [*derived, Change(maps.open_maps("content")), Verdicts(maps.open_maps("verdicts")), Label(), Hover(), Review()]


def _open_scene(model: Model, maps: MapStore, overlays: list[Overlay] | None = None) -> Scene:
    """Open a model as a scene with overlays for what extraction said, by shape and read by
    address, followed by nolli's own overlays (or ``overlays`` if given). A shape never extracted
    for this model says nothing; a model made from this one answers for a namesake with the
    same values."""
    said = [maps.precomputed_overlay(n) for n in vocabulary.SHAPES]
    return Scene(model, [*said, *(_make_overlays(maps) if overlays is None else overlays)])


def _open_commit_scene(store: ModelStore, commit: CommitKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """Open a commit's stored model as a scene; return None if the store does not have it."""
    m = store.load_model(commit)
    return None if m is None else _open_scene(m, store, overlays)


def _open_change_scene(store: ModelStore, change: ChangeKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """Open the scene of a change between two stored commits, or return None if the change or either
    commit is not stored. The scene uses the merged model kept with the change (or merges one here
    if none was kept), puts what the change says about itself in ``meta["change"]``, and answers
    its own overlays from what was kept at diff time, computing those that were not kept."""
    about = store.load_change(change)
    if about is None:
        return None
    base, head = (_open_commit_scene(store, c, overlays) for c in change.list_sides())
    if base is None or head is None:
        return None
    meta = _describe_scene_meta(base.model, head.model, about, store.open_maps("content").load(change.format_key()))
    scene = transformations.apply({"base": base, "head": head}, merge.union(change, meta, store.load_model(change)))
    return scene.with_overlays(*[store.caching_overlay(o) for o in _make_kept_overlays(store, change)])


def open(store: ModelStore, key: ModelKey, overlays: list[Overlay] | None = None) -> Scene | None:  # noqa: A001
    """Open the scene of a stored model, either a change merged from its commits or a single
    commit. Return None if the store does not have it; raise NotAllowed as the store does."""
    return _open_change_scene(store, key, overlays) if isinstance(key, ChangeKey) else _open_commit_scene(store, key, overlays)


def precompute(store: ModelStore, key: ModelKey) -> None:
    """Compute the overlays a stored model keeps (``_make_kept_overlays``; for a change, its two
    commits' first) and save their values in the store, so that opening it reads them instead of
    computing them. Raise KeyError if the store does not have the model."""
    for side in key.list_sides() if isinstance(key, ChangeKey) else ():
        precompute(store, side)
    scene = open(store, key)
    if scene is None:
        raise KeyError(f"no model {key.format_key()} to precompute")
    for o in _make_kept_overlays(store, key):
        store.precompute_overlay_values(scene, o)
