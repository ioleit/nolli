"""Helpers shared by the tests for building scenes of directories and changes and drawing them."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nolli.build import extract, extract_commit, list_content
from nolli.logic import transformations
from nolli.logic.formats.viewer import export_scene
from nolli.logic.transformations import projection
from nolli.logic.transformations.projection import ProjectionSpec
from nolli.model.keys import StringKey
from nolli.model.scene import Scene
from nolli.progress import Quiet
from nolli.review import changes
from nolli.scenes import merge_built, open_built


def open_dir(path: Path, key: str = "test@0") -> Scene:
    """The scene of the files under ``path`` as they are."""
    return open_built(*extract(path, key=StringKey(key)))


def spec(expanded: list[str] | None = None, preset: str | None = None, budget: int | None = None, **rest) -> ProjectionSpec:
    """A ProjectionSpec as the viewer's URL asks for one: unfolded containers, or a preset by
    name (the budget, for the default cut)."""
    if expanded is not None:
        expand = projection.Ids(tuple(expanded))
    elif preset not in (None, "default"):
        expand = changes.PRESETS.get(preset, projection.AtMost)()  # an unknown name: the default, as the server reads it
    else:
        expand = projection.AtMost(budget) if budget else projection.AtMost()
    return ProjectionSpec(expand=expand, **rest)


def draw(scene: Scene, **state) -> dict:
    """The viewer's JSON of ``scene`` projected with ``spec(**state)``."""
    return export_scene(transformations.apply(scene, projection.fold(spec(**state))))


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
