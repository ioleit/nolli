"""Overlays answered from a store (nolli/store/precomputed.py): what an overlay said about a
model kept and read back, as values only or with the overlay behind it, and nothing taken
from a namesake in another model, except what extraction said about an address."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import open_dir

from nolli.logic import transformations
from nolli.logic.transformations import projection
from nolli.logic.transformations.projection import ProjectionSpec
from nolli.model.keys import StringKey
from nolli.model.model import Entity, Model, Relation
from nolli.model.scene import Element, FixedOverlay, Overlay, Scene
from nolli.model.schema import RelationKind
from nolli.store import MemoryMaps, SourceElementOverlay


class Counting(Overlay):
    """Says the kind of every element and counts how often it was asked."""

    name = "counting"
    cache_version = "1"

    def __init__(self) -> None:
        self.asked: list = []

    def compute(self, element: Element) -> dict | None:
        self.asked.append(element.key)
        inside = [s for s in element.sources.get("source", ()) if s.key != element.key]
        return {"kind": element.kind, "inside": len(inside)}


@pytest.fixture
def scene(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("from . import b\n\nclass A:\n    pass\n\ndef f():\n    return b.B()\n")
    (tmp_path / "pkg" / "b.py").write_text("class B:\n    pass\n")
    return open_dir(tmp_path, key="test@precomputed")


MODULES = ProjectionSpec(expand=projection.Dirs(), groups=[], utilities=0)


def fold(scene):
    return transformations.apply(scene, projection.fold(MODULES))


def propose(scene):
    """A proposed grouping: a directory that is not there, holding pkg/ (same addresses otherwise)."""
    m = scene.model
    entities = [*m.entities.values(), Entity("repo:src", "directory")]
    edges = [e for e in m.edges if e != Relation(RelationKind.CONTAIN, "repo:", "repo:pkg")]
    edges += [Relation(RelationKind.CONTAIN, "repo:", "repo:src"), Relation(RelationKind.CONTAIN, "repo:src", "repo:pkg")]
    return Scene(Model(StringKey(f"{m.key.format_key()}|src"), entities, edges), [], {"source": scene}, {"source": None})


def test_precomputed_values_come_back_as_kept_and_nothing_else(scene):
    store = MemoryMaps()
    values = store.precompute_overlay_values(scene, Counting())
    assert values["repo:pkg/a.py"] == {"kind": "file", "inside": 0} and len(values) == len(scene.model.entities) + len(scene.model.edges)
    about = store.open_maps("counting").load_about(scene.model.key.format_key())
    assert about["overlay"] == "counting" and about["cache_version"] == "1" and about["at"]

    kept = scene.with_overlays(store.strictly_precomputed_overlay("counting"))
    assert kept.find_element("repo:pkg/a.py").get("counting") == {"kind": "file", "inside": 0}
    assert kept.read_all("counting") == values
    projection = fold(kept)
    assert projection.find_element("repo:pkg/a.py").get("counting") is None
    assert projection.read_all("counting") == {}


def test_what_extraction_said_holds_for_a_namesake(scene):
    assert isinstance(scene.overlays["files"].fallback, SourceElementOverlay)
    proposal = transformations.apply(scene, propose)
    a, src = proposal.find_element("repo:pkg/a.py"), proposal.find_element("repo:src")
    assert a.get("files") == scene.find_element("repo:pkg/a.py").get("files") and a.get("files")["module"]
    assert src.get("files") is None and src.get("symbols") is None  # no namesake: nothing was said
    assert set(proposal.read_all("files")) == set(scene.read_all("files"))
    edge = next(e for e in proposal.model.list_edges() if e.src == "repo:pkg/a.py")
    assert proposal.find_element(edge.id).get("references") == scene.find_element(edge.id).get("references")
    assert src.get("hover") is None and "a" in a.get("hover")["text"]  # nothing to say about what the source does not have
    # the proposal folds like any scene: roles, modules and references are read off it
    projection = fold(proposal)
    assert not projection.model.has("repo:src") and projection.model.has("repo:pkg")  # a folder holding one folder is drawn as that one
    assert projection.find_element("repo:pkg/a.py").get("label")["text"] == "a"
    assert projection.find_element("repo:pkg/a.py").get("files")["module"]  # a box that is a node of its source
    assert projection.find_element("repo:pkg").get("projection")["container"]

def test_caching_overlay_reads_what_is_kept_and_computes_the_rest(scene):
    store = MemoryMaps()
    store.precompute_overlay_values(scene, Counting())
    behind = Counting()
    kept = scene.with_overlays(store.caching_overlay(behind))
    assert kept.find_element("repo:pkg/a.py").get("counting") == {"kind": "file", "inside": 0}
    assert behind.asked == []  # answered from the store

    projection = fold(kept)  
    folded = projection.find_element("repo:pkg/a.py").get("counting")
    assert folded["kind"] == "file" and folded["inside"] >= 1  # the class folded into the file's box
    assert behind.asked == ["repo:pkg/a.py"]
    assert projection.read_all("counting") == fold(scene.with_overlays(Counting())).read_all("counting")  # as computed live
    assert all(projection.model.has(k) for k in behind.asked)  # never asked about the source's elements

def test_values_of_another_version_are_not_read(scene):
    store = MemoryMaps()
    store.precompute_overlay_values(scene, Counting())

    class Counting2(Counting):
        cache_version = "2"

    behind = Counting2()
    kept = scene.with_overlays(store.caching_overlay(behind))
    assert kept.find_element("repo:pkg/a.py").get("counting") == {"kind": "file", "inside": 0}
    assert behind.asked == ["repo:pkg/a.py"]  # computed: what was kept is version 1's
    assert kept.read_all("counting") == store.precompute_overlay_values(scene, Counting2())  # kept again, this version's
    assert store.open_maps("counting").load_about(scene.model.key.format_key())["cache_version"] == "2"


def test_an_overlay_without_a_version_reads_whatever_was_kept(scene):
    store = MemoryMaps()
    store.open_maps("files").save(scene.model.key.format_key(), {"repo:pkg/a.py": {"role": "kept"}}, {"cache_version": "fs-9"})
    assert store.precomputed_overlay("files").compute(scene.find_element("repo:pkg/a.py")) == {"role": "kept"}


def test_values_kept_without_a_version_are_read(scene):
    store = MemoryMaps()
    store.open_maps("counting").save(scene.model.key.format_key(), {"repo:pkg/a.py": {"kind": "from outside"}})  # as an extractor's map is
    behind = Counting()
    kept = scene.with_overlays(store.caching_overlay(behind))
    assert kept.find_element("repo:pkg/a.py").get("counting") == {"kind": "from outside"}
    assert kept.find_element("repo:pkg/b.py").get("counting") is None  # the map has nothing for it: not a miss
    assert behind.asked == []


def test_the_default_cache_version_is_the_class_source(scene):
    class One(Overlay):
        name = "one"

        def compute(self, element):
            return {"n": 1}

    class Two(Overlay):
        name = "one"

        def compute(self, element):
            return {"n": 2}

    assert One().cache_version != Two().cache_version and len(One().cache_version) == 12
    assert Counting().cache_version == "1"  # set by the class
    assert FixedOverlay("x", {}, cache_version="fs-3").cache_version == "fs-3" and FixedOverlay("x", {}).cache_version == ""


def test_a_scene_is_saved_and_opened_whole(scene):
    store = MemoryMaps()
    live = scene.with_overlays(Counting())
    store.save_scene(live)
    assert store.load_model(scene.model.key) is scene.model
    assert "counting" in store.list_map_names(scene.model.key.format_key()) and "files" in store.list_map_names(scene.model.key.format_key())

    behind = Counting()
    opened = store.open_scene(scene.model.key, [behind])
    assert opened.overlays["counting"].fallback is behind and opened.overlays["files"].fallback is None
    assert opened.find_element("repo:pkg/a.py").get("counting") == {"kind": "file", "inside": 0} and behind.asked == []
    assert opened.find_element("repo:pkg/a.py").get("files")["module"]
    assert store.open_scene("nobody@0") is None
