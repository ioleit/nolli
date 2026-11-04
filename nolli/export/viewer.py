"""A projected scene as JSON, as the viewer reads it (``/api/scene``): every drawn box and
edge with the properties every overlay gives it, under the overlay's name (``projection``,
``label``, ``hover``, ``change``, ``verdicts``, ``review``, and whatever else the scene has).
An overlay that says ``export = False`` (an extractor's word, read by the others) is left out.
Boxes come in drawing order, each naming its drawn parent; ids are stable across states
(addresses, ``<id>#`` for a module's own code, ``kind|src|dst|subkind`` for edges), so a
client can diff two scenes to animate the step between them."""

from __future__ import annotations

from collections import defaultdict

from ..scene import Element, Scene
from .mermaid import find_root

FORMAT = 1


def list_exported(scene: Scene) -> list[str]:
    return [n for n, o in scene.overlays.items() if getattr(o, "export", True)]


def describe(el: Element, names: list[str]) -> dict:
    return {n: v for n in names if (v := el.get(n)) is not None}


def export_scene(scene: Scene) -> dict:
    meta = scene.model.meta.get("projection", {})
    names = list_exported(scene)
    order = meta.get("order") or sorted(scene.model.kinds)
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
    root = find_root(scene)
    if root is not None and "base" in root.sources:
        from ..overlays.change import Links, index_changes, summarize_change

        statuses: dict[str, int] = defaultdict(int)
        for e in edges:
            if e.get("change"):
                statuses[e["change"]["status"]] += 1
        d = summarize_change(root)
        out["diff"] = {**{k: d.get(k) for k in ("base", "head", "base_short", "head_short", "remote", "pr", "files_changed", "added",
                                                "removed", "title")},
                       "link": Links(root).change(), "drawn": dict(statuses), "changed_entities": len(index_changes(root))}
    return out
