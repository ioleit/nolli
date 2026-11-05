"""Scenes of models: a model with the extractors' overlays and nolli's, on their storage. A model
just extracted and kept nowhere (``open_built``, ``merge_built``), or one kept in a store (a
commit, or a change merged from its two commits: ``open_stored``)."""

from __future__ import annotations

from collections.abc import Mapping

from nolli import transformations
from nolli.extractors import overlays as overlays_lib
from nolli.model import Key, Model
from nolli.model.keys import ChangeKey, CommitKey, ModelKey
from nolli.model.scene import Overlay, Scene
from nolli.store import ModelStore
from nolli.store.maps import Maps, MapStore, MemoryMaps
from nolli.transformations import merge


def make_overlays(maps: MapStore) -> list[Overlay]:
    """The overlays nolli shows on any scene, on top of the extractors'."""
    from nolli.formats.descriptions.hover import Hover
    from nolli.formats.descriptions.label import Label
    from nolli.review.card import Review
    from nolli.review.verdicts import Verdicts
    from nolli.transformations.merge import Change

    return [Change(maps.open_maps("content")), Verdicts(maps.open_maps("verdicts")), Label(), Hover(), Review()]


def open_scene(model: Model, maps: MapStore, overlays: list[Overlay] | None = None) -> Scene:
    """An extracted model with its extractors' overlays and nolli's (or ``overlays``)."""
    return Scene(model, [*overlays_lib.make_base_overlays(model, maps), *(make_overlays(maps) if overlays is None else overlays)])


def open_commit_scene(store: ModelStore, commit: CommitKey, overlays: list[Overlay] | None = None) -> Scene | None:
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
            **join_readers(base.model.meta, head.model.meta),
            "change": merge.describe_change(about, store.open_maps("content").load(change.format_key()))}
    return transformations.apply({"base": base, "head": head}, merge.union(change.format_key(), meta))


def join_readers(base: dict, head: dict) -> dict:
    """Who says what in a change: the overlays both sides' extractors wrote, each language's symbols."""
    return {"overlays": sorted(set(base.get("overlays") or []) | set(head.get("overlays") or [])),
            "symbols": {**(base.get("symbols") or {}), **(head.get("symbols") or {})}}


def open_built(model: Model, maps: Maps, overlays: list[Overlay] | None = None) -> Scene:
    """A model just extracted, not kept anywhere, as a scene."""
    store = MemoryMaps({n: {model.key: v} for n, v in maps.items()})
    return open_scene(model, store, overlays)


def merge_built(base: tuple[Model, Maps], head: tuple[Model, Maps], key: str, about: dict, content: Mapping[Key, dict],
                overlays: list[Overlay] | None = None) -> Scene:
    """A change of two models just extracted, not kept anywhere (``nolli diff``, tests)."""
    store = MemoryMaps()
    for m, maps in (base, head):
        for n, v in maps.items():
            store.open_maps(n).save(m.key, v)
    store.open_maps("content").save(key, content)
    bs, hs = (open_scene(m, store, overlays) for m, _ in (base, head))
    meta = {"repo": hs.model.meta.get("repo"), "commit": about.get("head", "")[:7], "files": hs.model.meta.get("files"),
            "languages": {**bs.model.meta.get("languages", {}), **hs.model.meta.get("languages", {})},
            **join_readers(bs.model.meta, hs.model.meta), "change": merge.describe_change(about, content)}
    return transformations.apply({"base": bs, "head": hs}, merge.union(key, meta))


def open_stored(store: ModelStore, key: ModelKey, overlays: list[Overlay] | None = None) -> Scene | None:
    """The scene of a kept model: a change merged from its commits, or one commit."""
    return open_change_scene(store, key, overlays) if isinstance(key, ChangeKey) else open_commit_scene(store, key, overlays)
