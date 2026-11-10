"""Opens models as scenes, with what extraction said and nolli's overlays.

A model may be one just extracted and kept nowhere, or one kept in a store: a commit, or a
change merged from its two commits.
"""

from __future__ import annotations

from collections.abc import Mapping

from nolli import transformations
from nolli.model import vocabulary
from nolli.model.keys import ChangeKey, CommitKey, ModelKey
from nolli.model.model import Key, Model
from nolli.model.scene import Overlay, Scene
from nolli.store import ModelStore
from nolli.store.maps import Maps, MapStore, MemoryMaps
from nolli.transformations import merge


def _make_derived_overlays() -> list[Overlay]:
    """What nolli derives from any model's facts and keeps with it at build time
    (``keep_commit_scene``, ``keep_change_scene``): how its nodes are called (``names``)."""
    from nolli.formats.descriptions.names import Names

    return [Names()]


def _make_change_overlays(maps: MapStore) -> list[Overlay]:
    """What a change says about itself, computed from its sides: what changed (``change``) and
    which numbered changes each node and edge is about (``review``). Kept with the change at
    diff time (``keep_change_scene``) and read back from there (``open_change_scene``)."""
    from nolli.review.card import Review
    from nolli.transformations.merge import Change

    return [Change(maps.open_maps("content")), Review()]


def describe_change_meta(base: Model, head: Model, about: dict) -> dict:
    """A change's merged model's ``meta``, from its sides: the repository, the head commit, its
    files and languages. What the change says about itself (``about``, kept as change.json) is
    added when the scene is opened (``meta["change"]``, ``merge.describe_change``), so that
    what it says is read from one place."""
    return {"repo": head.meta.get("repo"), "commit": about.get("head", "")[:7], "files": head.meta.get("files"),
            "languages": {**base.meta.get("languages", {}), **head.meta.get("languages", {})}}


def _describe_scene_meta(base: Model, head: Model, about: dict, content: Mapping[Key, dict]) -> dict:
    return {**describe_change_meta(base, head, about), "change": merge.describe_change(about, content)}


def _make_overlays(maps: MapStore) -> list[Overlay]:
    """The overlays nolli shows on any scene, on top of what extraction said."""
    from nolli.formats.descriptions.hover import Hover
    from nolli.formats.descriptions.label import Label
    from nolli.review.card import Review
    from nolli.review.verdicts import Verdicts
    from nolli.transformations.merge import Change

    derived = [maps.caching_overlay(o) for o in _make_derived_overlays()]
    return [*derived, Change(maps.open_maps("content")), Verdicts(maps.open_maps("verdicts")), Label(), Hover(), Review()]


def _open_scene(model: Model, maps: MapStore, overlays: list[Overlay] | None = None) -> Scene:
    """A model as a scene with nolli's default overlays: what extraction said, by shape, read
    by address (a shape never extracted for this model says nothing; a model made from this
    one answers for a namesake with the same), and nolli's own (or ``overlays``)."""
    said = [maps.precomputed_overlay(n) for n in vocabulary.SHAPES]
    return Scene(model, [*said, *(_make_overlays(maps) if overlays is None else overlays)])


def open_commit_scene(store: ModelStore, commit: CommitKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """A commit's stored model as a scene, None if the store does not have it."""
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
    """The change of its two stored commits: its merged model as kept with it (merged here for a
    change kept without one), what it says about itself in its model's ``meta["change"]``, and
    its own overlays answered from what was kept at diff time where it was, computed otherwise."""
    about = store.load_change(change)
    if about is None:
        return None
    base, head = (open_commit_scene(store, c, overlays) for c in change.list_sides())
    if base is None or head is None:
        return None
    meta = _describe_scene_meta(base.model, head.model, about, store.open_maps("content").load(change.format_key()))
    scene = transformations.apply({"base": base, "head": head}, merge.union(change.format_key(), meta, store.load_model(change)))
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
    """A model just extracted, not kept anywhere, as a scene."""
    store = MemoryMaps({n: {model.key: v} for n, v in maps.items()})
    return _open_scene(model, store, overlays)


def merge_built(base: tuple[Model, Maps], head: tuple[Model, Maps], key: str, about: dict, content: Mapping[Key, dict],
                overlays: list[Overlay] | None = None) -> Scene:
    """A change of two models just extracted, not kept anywhere (``nolli diff``, tests)."""
    store = MemoryMaps()
    for m, maps in (base, head):
        for n, v in maps.items():
            store.open_maps(n).save(m.key, v)
    store.open_maps("content").save(key, content)
    bs, hs = (_open_scene(m, store, overlays) for m, _ in (base, head))
    return transformations.apply({"base": bs, "head": hs}, merge.union(key, _describe_scene_meta(bs.model, hs.model, about, content)))


def _open_stored(store: ModelStore, key: ModelKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """The scene of a kept model: a change merged from its commits, or one commit."""
    return open_change_scene(store, key, overlays) if isinstance(key, ChangeKey) else open_commit_scene(store, key, overlays)
