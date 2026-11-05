"""A projected scene as JSON, as the viewer reads it (``/api/scene``): every drawn box and
edge with the properties every overlay gives it, under the overlay's name (``projection``,
``label``, ``hover``, ``change``, ``verdicts``, ``review``, and whatever else the scene has).
The extractors' overlays (read by the others) are left out.
Boxes come in drawing order, each naming its drawn parent; ids are stable across states
(addresses, ``<id>#`` for a module's own code, ``kind|src|dst|subkind`` for edges), so a
client can diff two scenes to animate the step between them."""

from __future__ import annotations

from nolli.formats import mermaid
from nolli.model.scene import Element, Scene

FORMAT = 1


def list_extracted(scene: Scene) -> set[str]:
    """The overlays the extractors wrote (each extracted model names its own), all the way down."""
    out, stack = {"facts"}, [scene]
    while stack:
        s = stack.pop()
        out |= set(s.model.meta.get("overlays") or [])
        stack.extend(s.sources.values())
    return out


def list_exported(scene: Scene) -> list[str]:
    """Every overlay but the extractors': they are read by the others, not shown as they are."""
    left_out = list_extracted(scene)
    return [n for n in scene.overlays if n not in left_out]


def describe(el: Element, names: list[str]) -> dict:
    return {n: v for n in names if (v := el.get(n)) is not None}


def export_scene(scene: Scene) -> dict:
    meta = scene.model.meta.get("projection", {})
    names = list_exported(scene)
    order = mermaid.list_drawing_order(scene)
    nodes = [{"id": x, "kind": scene.model.kinds[x], "parent": scene.model.find_parent(x), **describe(scene.find_element(x), names)}
             for x in order]
    edges = [{"key": el.id, "kind": el.key.kind, "subkind": el.key.subkind, "src": el.key.src, "dst": el.key.dst, **describe(el, names)}
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
