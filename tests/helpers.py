"""Helpers shared by the tests for building scenes of directories and changes and drawing them."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nolli.build import extract, extract_commit, list_content
from nolli.scenes import merge_built, open_built
from nolli.formats.viewer import export_scene
from nolli.progress import Quiet
from nolli.model.keys import StringKey
from nolli.model.scene import Scene
from nolli import transformations
from nolli.transformations import projection
from nolli.transformations.projection import ProjectionSpec


def open_dir(path: Path, key: str = "test@0") -> Scene:
    """The scene of the files under ``path`` as they are."""
    return open_built(*extract(path, key=StringKey(key)))


def draw(scene: Scene, **state) -> dict:
    """The viewer's JSON of ``scene`` projected with ``ProjectionSpec(**state)``."""
    return export_scene(transformations.apply(scene, projection.fold(ProjectionSpec(**state))))


def open_change(repo: Path, base: str, head: str, **about) -> Scene:
    """The change between two revisions of the git repository ``repo``, built here, kept nowhere."""
    def sha(rev: str) -> str:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{rev}^{{commit}}"],
                              check=True, capture_output=True, text=True).stdout.strip()

    b, h = sha(base), sha(head)
    sides = []
    for s in (b, h):
        m, maps = extract_commit(repo, s, repo.name, Quiet())
        m.key = StringKey(f"test@{s}")
        sides.append((m, maps))
    return merge_built(sides[0], sides[1], StringKey(f"test@{b}..{h}"), {"base": b, "head": h, **about}, list_content(repo, b, h, Quiet()))


def find_node(sc: dict, x: str) -> dict:
    return next(n for n in sc["nodes"] if n["id"] == x)
