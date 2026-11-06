"""Exports a scene as JSON for the Nolli viewer."""

from __future__ import annotations

from nolli.formats import mermaid
from nolli.model.scene import Element, Scene

FORMAT = 1
SHOWN = ("projection", "label", "hover", "change", "verdicts", "review")  # the overlays the viewer draws, in this order


def _describe(el: Element, names: list[str]) -> dict:
    return {n: v for n in names if (v := el.get(n)) is not None}


def export_scene(scene: Scene) -> dict:
    """A projected scene as JSON: every drawn box and edge with the properties the overlays
    the viewer draws give it (``SHOWN``), under the overlay's name. What extraction said is
    read by those, not shown as it is.

    Boxes come in drawing order, each naming its drawn parent. Ids are stable across states
    (addresses, ``<id>#`` for a module's own code, ``kind|src|dst|subkind`` for edges), so a
    client can diff two scenes to animate the step between them."""
    meta = scene.model.meta.get("projection", {})
    names = [n for n in SHOWN if n in scene.overlays]
    order = mermaid.list_drawing_order(scene)
    nodes = [{"id": x, "kind": scene.model.kinds[x], "parent": scene.model.find_parent(x), **_describe(scene.find_element(x), names)}
             for x in order]
    edges = [{"key": el.id, "kind": el.key.kind, "subkind": el.key.subkind, "src": el.key.src, "dst": el.key.dst, **_describe(el, names)}
             for el in scene.list_edges()]
    residue = list(meta.get("residue", []))
    if meta.get("utilities"):  # named as the picture names them
        residue.append("utilities: " + ", ".join((scene.find_element(u).get("label") or {}).get("prose", u) for u in meta["utilities"]
                                                 if scene.has(u)))
    out = {"format": FORMAT, "state": meta.get("state"), "nodes": nodes, "edges": edges, "groups": meta.get("groups", []),
           "roles": meta.get("roles", []), "residue": residue, "stats": dict(meta.get("stats", {})),
           "overlays": names}
    d = scene.model.meta.get("change")
    if d:  # a picture of a change: what the change says about itself (``meta["change"]``)
        out["diff"] = {k: d.get(k) for k in ("base", "head", "base_short", "head_short", "remote", "pr", "files_changed", "added",
                                            "removed", "title", "link")}
    return out
