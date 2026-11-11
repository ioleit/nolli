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
from nolli.store.maps import Maps, MapStore, MemoryMaps


def _make_derived_overlays() -> list[Overlay]:
    """Make the overlays nolli derives from any model's facts and keeps with it at build time
    (in ``keep_commit_scene`` and ``keep_change_scene``): what its nodes are
    called (``names``)."""
    from nolli.logic.formats.descriptions.names import Names

    return [Names()]


def _make_change_overlays(maps: MapStore) -> list[Overlay]:
    """Make the overlays that compute what a change says about itself from its sides: what changed
    (``change``) and which numbered changes each node and edge is about (``review``). Their values
    are kept with the change at diff time (``keep_change_scene``) and read back from there
    (``open_change_scene``)."""
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


def open_commit_scene(store: ModelStore, commit: CommitKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """Open a commit's stored model as a scene; return None if the store does not have it."""
    m = store.load_model(commit)
    return None if m is None else _open_scene(m, store, overlays)


def keep_commit_scene(store: ModelStore, commit: CommitKey, by: dict | None = None) -> None:
    """Keep what nolli derives from a stored commit's facts (``_make_derived_overlays``) under
    its key, so that opening it reads that instead of computing it."""
    scene = open_commit_scene(store, commit)
    if scene is None:
        raise KeyError(f"no commit {commit.format_key()} to keep")
    for o in _make_derived_overlays():
        store.precompute_overlay_values(scene, o, by)


def open_change_scene(store: ModelStore, change: ChangeKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """Open the scene of a change between two stored commits, or return None if the change or either
    commit is not stored. The scene uses the merged model kept with the change (or merges one here
    if none was kept), puts what the change says about itself in ``meta["change"]``, and answers
    its own overlays from what was kept at diff time, computing those that were not kept."""
    about = store.load_change(change)
    if about is None:
        return None
    base, head = (open_commit_scene(store, c, overlays) for c in change.list_sides())
    if base is None or head is None:
        return None
    meta = _describe_scene_meta(base.model, head.model, about, store.open_maps("content").load(change.format_key()))
    scene = transformations.apply({"base": base, "head": head}, merge.union(change, meta, store.load_model(change)))
    return scene.with_overlays(*[store.caching_overlay(o) for o in [*_make_derived_overlays(), *_make_change_overlays(store)]])


def keep_change_scene(store: ModelStore, change: ChangeKey, by: dict | None = None) -> None:
    """Keep what a stored change says about itself (``_make_change_overlays``) under its key, so
    that ``open_change_scene`` reads it instead of computing it on every read."""
    for c in change.list_sides():
        keep_commit_scene(store, c, by)
    scene = open_change_scene(store, change)
    if scene is None:
        raise KeyError(f"no change {change.format_key()} to keep")
    for o in [*_make_derived_overlays(), *_make_change_overlays(store)]:
        store.precompute_overlay_values(scene, o, by)


def open_built(model: Model, maps: Maps, overlays: list[Overlay] | None = None) -> Scene:
    """Open a model that was just extracted, and is not kept anywhere, as a scene."""
    store = MemoryMaps({n: {model.key.format_key(): v} for n, v in maps.items()})
    return _open_scene(model, store, overlays)


def merge_built(base: tuple[Model, Maps], head: tuple[Model, Maps], key: ModelKey, about: dict, content: Mapping[Key, dict],
                overlays: list[Overlay] | None = None) -> Scene:
    """Merge two models that were just extracted, and are not kept anywhere, into a change's scene."""
    store = MemoryMaps()
    for m, maps in (base, head):
        for n, v in maps.items():
            store.open_maps(n).save(m.key.format_key(), v)
    store.open_maps("content").save(key.format_key(), content)
    bs, hs = (_open_scene(m, store, overlays) for m, _ in (base, head))
    return transformations.apply({"base": bs, "head": hs}, merge.union(key, _describe_scene_meta(bs.model, hs.model, about, content)))


def _open_stored(store: ModelStore, key: ModelKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """Open the scene of a stored model, either a change merged from its commits or a single commit."""
    return open_change_scene(store, key, overlays) if isinstance(key, ChangeKey) else open_commit_scene(store, key, overlays)
