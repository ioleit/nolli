"""Writes the brief a review agent gets for a change: ``nolli/skill/review.md`` filled in with the
change's title, the diagram it reviews and the change's description."""

from __future__ import annotations

import re
from importlib.resources import files

from nolli.logic import transformations
from nolli.logic.formats import mermaid as mermaid_format
from nolli.logic.transformations import merge, projection
from nolli.logic.transformations.merge import Links, presets
from nolli.model.scene import Scene


def make_review_spec() -> projection.ProjectionSpec:
    """Return the projection a change is reviewed on: every area that changed in source code
    unfolded (``presets.Changes``), the rest as the default cut draws it. Its key
    (``projection.make_key``) is the same for the same change, so comments made on it stay valid."""
    return projection.ProjectionSpec(expand=presets.Changes())


def project_for_review(scene: Scene) -> Scene:
    """Project a change as its review shows it (``make_review_spec``)."""
    return transformations.apply(scene, projection.fold(make_review_spec()))


def make_brief(scene: Scene, mermaid: str | None = None, description: str | None = None) -> str:
    """Write the brief of the change ``scene`` shows: its title, how to review it and answer, the
    diagram of everything that changed (``mermaid``, drawn from ``project_for_review`` if not
    given) and the change's description (what it says about itself if not given)."""
    d = scene.model.meta["change"]
    mermaid = mermaid_format.render(project_for_review(scene)) if mermaid is None else mermaid
    description = d.get("description") if description is None else description
    links = Links(d)
    head = f"# Architecture review: {d.get('title') or 'change'}\n\n"
    head += (f"{d['base_short']} → {d['head_short']}" + (f", PR #{d['pr']}" if d.get("pr") else "")
             + f": {d['files_changed']} files, +{d['added']} −{d['removed']} lines"
             + (f"\n{links.change()}" if links.change() else "") + "\n")
    if description:  # its headings go two levels down, under this one
        description = re.sub(r"^(#{1,4}) ", r"##\1 ", description.strip(), flags=re.MULTILINE)
    desc = f"\n## The description\n\nRead this after the diagram (step 2).\n\n{description}\n" if description else ""
    template = files("nolli").joinpath("skill/review.md").read_text()
    return template.replace("{head}", head, 1).replace("{diagram}", mermaid, 1).replace("{description}", desc, 1)


def format_brief(scene: Scene, _options: object = None) -> dict:
    """Write the brief of the change a scene shows or was drawn from, and return it as JSON.
    Raise ValueError if the scene is not of a change. The brief draws its own diagram of the
    change, so it ignores any projection the scene was drawn with."""
    source = scene.sources.get("source", scene)
    if not merge.is_change(source):
        raise ValueError("not a change")
    return {"brief": make_brief(source)}
