"""Helpers shared by the tests for building scenes of directories and changes and drawing them."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nolli import operations
from nolli.logic import transformations
from nolli.logic.formats.viewer import export_scene
from nolli.logic.sources import LocalRepository
from nolli.logic.transformations import projection
from nolli.logic.transformations.projection import ProjectionSpec
from nolli.model.keys import ModelKey, StringKey
from nolli.model.model import Model
from nolli.model.scene import Scene
from nolli.logic.transformations.merge import presets as changes
from nolli.store import MemoryStore
from nolli.store.maps import Maps


def open_dataset(ds: Path) -> tuple[MemoryStore, ModelKey]:
    """Import the dataset directory ``ds`` (``nolli build -o``) into memory and return the store and the key."""
    from nolli import cli

    store = MemoryStore()
    return store, cli.import_dataset(store, ds)


def open_dir(path: Path, key: str = "test") -> Scene:
    """Build the files under ``path`` as they are into memory, and open them as a scene."""
    store = MemoryStore()
    return operations.open_scene(store, operations.build_files(store, path, StringKey(key)))


def open_extracted(model: Model, maps: Maps, key: str = "test") -> Scene:
    """Keep a model and maps just extracted (``Facts.split``) in memory, and open them as a scene."""
    store = MemoryStore()
    store.save_model(StringKey(key), model, maps)
    return operations.open_scene(store, StringKey(key))


def spec(expanded: list[str] | None = None, preset: str | None = None, budget: int | None = None, **rest) -> ProjectionSpec:
    """Make a ProjectionSpec as the viewer's URL asks for one: unfolded containers, or a preset by
    name (with the budget, for the default cut)."""
    if expanded is not None:
        expand = projection.Ids(tuple(expanded))
    elif preset not in (None, "default"):
        expand = changes.PRESETS.get(preset, projection.AtMost)()  # an unknown name: the default, as the server reads it
    else:
        expand = projection.AtMost(budget) if budget else projection.AtMost()
    return ProjectionSpec(expand=expand, **rest)


def draw(scene: Scene, **state) -> dict:
    """Project ``scene`` with ``spec(**state)`` and return the viewer's JSON of it."""
    return export_scene(transformations.apply(scene, projection.fold(spec(**state))))


def build_change(repo: Path, base: str, head: str, **about) -> tuple[MemoryStore, ModelKey]:
    """Build the change between two revisions of the git repository ``repo`` into memory, and
    return the store and the change's key."""
    def sha(rev: str) -> str:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{rev}^{{commit}}"],
                              check=True, capture_output=True, text=True).stdout.strip()

    store, local = MemoryStore(), LocalRepository(repo)
    key = local.find_repo().make_change(sha(base), sha(head))
    operations.build_target(store, operations.Target("key", key=key), local, about=about)
    return store, key


def open_change(repo: Path, base: str, head: str, **about) -> Scene:
    """Build the change between two revisions of the git repository ``repo`` into memory, and open
    it as a scene, as the server does."""
    store, key = build_change(repo, base, head, **about)
    return operations.open_scene(store, key)


def find_node(sc: dict, x: str) -> dict:
    return next(n for n in sc["nodes"] if n["id"] == x)


def list_changes(scene: Scene, **state) -> list[dict]:
    """Return the review panel's rows for a change: the changed boxes and edges of the changes
    preset (or of ``spec(**state)``), as ``f=table&required_overlay=change&overlays=change,label``."""
    from nolli.logic.formats import table
    from nolli.logic.formats.options import TableFormatOptions

    drawn = transformations.apply(scene, projection.fold(spec(**(state or {"preset": "changes"}))))
    return table.export_table(drawn, TableFormatOptions(overlays=["change", "label"], required_overlay="change"))["rows"]
