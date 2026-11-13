"""Exports a scene as JSON for the Nolli viewer."""

from __future__ import annotations

from collections.abc import Mapping

from nolli.logic.formats import mermaid
from nolli.model.model import Key
from nolli.model.scene import Scene

FORMAT = 1
SHOWN = ("projection", "label", "hover", "change", "comments")  # the overlays the viewer draws, in this order


def _describe(key: Key, said: dict[str, Mapping[Key, dict]]) -> dict:
    return {n: v for n, values in said.items() if (v := values.get(key)) is not None}


def export_scene(scene: Scene) -> dict:
    """Export a projected scene as JSON for the viewer. Each drawn box and edge carries the
    properties given to it by the overlays the viewer draws (``SHOWN``), under each overlay's
    name. What extraction said is read by those overlays, not shown as it is.

    Boxes come in drawing order, each naming its drawn parent. Ids are stable across states
    (addresses, ``<id>#`` for a module's own code, ``kind|src|dst|subkind`` for edges), so a
    client can diff two scenes to animate the step between them."""
    meta = scene.model.meta.get("projection", {})
    names = [n for n in SHOWN if n in scene.overlays]
    said = {n: scene.read_all(n) for n in names}  # each overlay asked once, for the whole scene
    order = mermaid.list_drawing_order(scene)
    nodes = [{"id": x, "kind": scene.model.entities[x].kind, "parent": scene.model.find_parent(x), **_describe(x, said)} for x in order]
    edges = [{"key": e.id, "kind": e.kind, "subkind": e.subkind, "src": e.src, "dst": e.dst, **_describe(e.id, said)}
             for e in scene.model.list_edges()]
    residue = list(meta.get("residue", []))
    if meta.get("utilities"):  # each utility by its label's prose
        residue.append("utilities: " + ", ".join((scene.find_element(u).get("label") or {}).get("prose", u) for u in meta["utilities"]
                                                 if scene.has(u)))
    out = {"format": FORMAT, "state": meta.get("state"), "nodes": nodes, "edges": edges, "groups": meta.get("groups", []),
           "roles": meta.get("roles", []), "residue": residue, "stats": dict(meta.get("stats", {})),
           "overlays": names}
    d = scene.model.meta.get("change")
    if d:  # the scene is drawn from a change
        out["diff"] = {k: d.get(k) for k in ("base", "head", "base_short", "head_short", "remote", "pr", "files_changed", "added",
                                            "removed", "title", "link")}
    return out
