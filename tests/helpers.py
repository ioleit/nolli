"""What the tests share: a directory's facts, its scene, a picture of it, a change of two commits."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nolli.build import extract, extract_commit, extract_facts, list_content
from nolli.scenes import merge_built, open_built
from nolli.formats.viewer import export_scene
from nolli.progress import Quiet
from nolli.model.scene import Scene
from nolli.transformations.projection import ProjectionSpec, project


def open_dir(path: Path, key: str = "test@0") -> Scene:
    """The scene of the files under ``path`` as they are."""
    return open_built(*extract(path, key=key))


def draw(scene: Scene, **state) -> dict:
    """The viewer's JSON of the picture ``ProjectionSpec(**state)`` draws of ``scene``."""
    return export_scene(project(scene, ProjectionSpec(**state)))


def open_change(repo: Path, base: str, head: str, **about) -> Scene:
    """The change between two revisions of the git repository ``repo``, built here, kept nowhere."""
    def sha(rev: str) -> str:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{rev}^{{commit}}"],
                              check=True, capture_output=True, text=True).stdout.strip()

    b, h = sha(base), sha(head)
    sides = []
    for s in (b, h):
        m, maps = extract_commit(repo, s, repo.name, Quiet())
        m.key = f"test@{s}"
        sides.append((m, maps))
    return merge_built(sides[0], sides[1], f"test@{b}..{h}", {"base": b, "head": h, **about}, list_content(repo, b, h, Quiet()))


def find_node(sc: dict, x: str) -> dict:
    return next(n for n in sc["nodes"] if n["id"] == x)
