"""Exports a scene as a table, with a row for each node and edge and a column for each overlay."""

from __future__ import annotations

from nolli.logic.formats import mermaid
from nolli.logic.formats.options import TableFormatOptions
from nolli.model.scene import Scene


def export_table(scene: Scene, options: TableFormatOptions) -> dict:
    """Export the scene's nodes (in drawing order) and edges as rows. Each row carries the value
    of each overlay in ``options.overlays`` under the overlay's name, None where the overlay says
    nothing about it. With ``options.required_overlay``, only the elements that overlay has a value
    for are rows; it is a column only if it is also in ``overlays``. Raise ValueError for an
    overlay the scene does not have."""
    required = options.required_overlay
    names = list(options.overlays)
    missing = [n for n in [*names, *([required] if required else [])] if n not in scene.overlays]
    if missing:
        raise ValueError(f"no overlay {', '.join(missing)} on this scene (it has {', '.join(sorted(scene.overlays))})")
    said = {n: scene.read_all(n) for n in {*names, *([required] if required else [])}}  # each overlay asked once
    keep = (lambda key: True) if required is None else (lambda key: said[required].get(key) is not None)
    m = scene.model
    rows = [{"id": x, "kind": m.entities[x].kind, "parent": m.find_parent(x), **{n: said[n].get(x) for n in names}}
            for x in mermaid.list_drawing_order(scene) if keep(x)]
    rows += [{"id": e.id, "kind": e.kind, "subkind": e.subkind, "src": e.src, "dst": e.dst, **{n: said[n].get(e.id) for n in names}}
             for e in m.list_edges() if keep(e.id)]
    return {"overlays": names, "rows": rows}
