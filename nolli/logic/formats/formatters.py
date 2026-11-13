"""Turns a scene, projected or not, into a JSON body, with one formatter for each format.

A formatter is a function ``(scene, options) -> dict``. Those with options of their own take
their options model (nolli/logic/formats/options.py); the others ignore the second argument.
Which scene a formatter gets is its caller's choice (the server projects one unless asked not
to). A formatter that cannot format the scene it gets raises ValueError.

A server may add formatters of its own (nolli/server/serve.py)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nolli.logic import comments as comments_lib
from nolli.logic.formats import brief, mermaid, report, table, viewer
from nolli.logic.formats.options import MermaidFormatOptions, TableFormatOptions
from nolli.model.scene import Scene

Formatter = Callable[[Scene, Any], dict]


def _format_viewer(scene: Scene, _options: object = None) -> dict:
    """Export the scene as the viewer's JSON (``viewer.export_scene``), and add a ``dataset`` block
    that says which model it shows: its key, repository, commit and size. If the scene is a
    projection, the block describes the model it was drawn from."""
    out = viewer.export_scene(scene)
    m = scene.sources.get("source", scene).model
    meta = m.meta or {}
    out["dataset"] = {"key": m.key.format_key(), "repo": meta.get("repo"), "commit": meta.get("commit"),
                      "files": meta.get("files"), "entities": len(m.entities), "relations": len(m.edges)}
    return out


def _format_mermaid(scene: Scene, options: MermaidFormatOptions) -> dict:
    """Render the scene as Mermaid, with the scene id each Mermaid box id stands for."""
    return {"mermaid": mermaid.render(scene, fade=options.fade), "ids": mermaid.list_ids(scene)}


def _format_table(scene: Scene, options: TableFormatOptions) -> dict:
    """Export the scene's nodes and edges as rows of overlay values (``table.export_table``)."""
    return table.export_table(scene, options)


def _format_report(scene: Scene, _options: object = None) -> dict:
    """Build the report of a projected scene and the model it was drawn from
    (``report.make_report``). Raise ValueError for a scene that is not a projection."""
    if "projection" not in scene.model.meta:
        raise ValueError("a report describes a projection: ask for one")
    return report.make_report(scene.sources["source"], scene)


def _format_comments(scene: Scene, _options: object = None) -> dict:
    """List every comment kept on the models the scene was drawn from, whether it shows on the
    scene or not, each with what it takes to go to it (``comments.describe_comments``)."""
    o = scene.overlays.get(comments_lib.Comments.name)
    found = o.list_all(scene) if isinstance(o, comments_lib.Comments) else []
    return {"comments": comments_lib.describe_comments(scene, found)}


FORMATS: dict[str, Formatter] = {"viewer": _format_viewer, "table": _format_table, "mermaid": _format_mermaid,
                                 "report": _format_report, "comments": _format_comments, "brief": brief.format_brief}
