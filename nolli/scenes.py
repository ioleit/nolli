"""Opens models as scenes, with what extraction said and nolli's overlays.

A model may be one just extracted and kept nowhere, or one kept in a store: a commit, or a
change merged from its two commits.
"""

from __future__ import annotations

from collections.abc import Mapping

from nolli import transformations
from nolli.model import Key, Model, vocabulary
from nolli.model.keys import ChangeKey, CommitKey, ModelKey
from nolli.model.scene import Overlay, Scene
from nolli.store import ModelStore
from nolli.store.maps import Maps, MapStore, MemoryMaps
from nolli.transformations import merge


def _make_overlays(maps: MapStore) -> list[Overlay]:
    """The overlays nolli shows on any scene, on top of what extraction said."""
    from nolli.formats.descriptions.hover import Hover
    from nolli.formats.descriptions.label import Label
    from nolli.review.card import Review
    from nolli.review.verdicts import Verdicts
    from nolli.transformations.merge import Change

    return [Change(maps.open_maps("content")), Verdicts(maps.open_maps("verdicts")), Label(), Hover(), Review()]


def _open_scene(model: Model, maps: MapStore, overlays: list[Overlay] | None = None) -> Scene:
    """A model as a scene with nolli's default overlays: what extraction said, by shape, read
    by address (a shape never extracted for this model says nothing; a model made from this
    one answers for a namesake with the same), and nolli's own (or ``overlays``)."""
    said = [maps.by_address(n) for n in vocabulary.SHAPES]
    return Scene(model, [*said, *(_make_overlays(maps) if overlays is None else overlays)])


def open_commit_scene(store: ModelStore, commit: CommitKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """A commit's stored model as a scene, None if the store does not have it."""
    m = store.load_model(commit)
    return None if m is None else _open_scene(m, store, overlays)


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
            "change": merge.describe_change(about, store.open_maps("content").load(change.format_key()))}
    return transformations.apply({"base": base, "head": head}, merge.union(change.format_key(), meta))


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
    meta = {"repo": hs.model.meta.get("repo"), "commit": about.get("head", "")[:7], "files": hs.model.meta.get("files"),
            "languages": {**bs.model.meta.get("languages", {}), **hs.model.meta.get("languages", {})},
            "change": merge.describe_change(about, content)}
    return transformations.apply({"base": bs, "head": hs}, merge.union(key, meta))


def _open_stored(store: ModelStore, key: ModelKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """The scene of a kept model: a change merged from its commits, or one commit."""
    return open_change_scene(store, key, overlays) if isinstance(key, ChangeKey) else open_commit_scene(store, key, overlays)
