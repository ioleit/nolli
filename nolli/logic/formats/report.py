"""Summarizes a scene in JSON. Might be deprecated."""

from __future__ import annotations

from collections import defaultdict

from nolli.model import schema
from nolli.model.scene import Scene


def _summarize(scene: Scene) -> dict:
    by_kind: dict[str, int] = defaultdict(int)
    for x in scene.model.entities.values():
        by_kind[x.kind] += 1
    rel: dict[str, int] = defaultdict(int)
    for e in scene.model.edges:
        rel[f"{e.kind}/{e.subkind}" if e.subkind else e.kind] += 1
    return {"entities": dict(sorted(by_kind.items())), "relations": dict(sorted(rel.items()))}


def make_report(scene: Scene, drawn: Scene) -> dict:
    """Build the ``report.json`` of a model. It holds the model's meta, the number of nodes and
    edges of each kind, the size of ``drawn``, the scene's default projection, the unresolved
    references, and for a change how much changed."""
    meta = scene.model.meta
    p = drawn.model.meta["projection"]
    report = {"meta": meta, "summary": _summarize(scene),
              "view": {"visible": p["stats"]["nodes"], "edges": p["stats"]["edges"], "internal": p["internal"],
                       "hidden_relations": p["hidden_relations"]},
              "unresolved": sorted(x.id for x in scene.model.entities.values() if x.kind == "unresolved"),
              "notes": meta.get("notes", {})}
    if "change" in meta:
        report["diff"] = _count_changes(scene)
    return report


def _count_changes(scene: Scene) -> dict:
    """How many nodes and edges changed, by kind and status (the ``change`` overlay's word)."""
    by: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for el in scene.list_nodes():
        d = el.get("change")
        if d:
            by["entities"][f"{el.kind}/{d['status']}"] += 1
            if (el.kind == "file" or el.kind in schema.SYMBOL_KINDS) and (d.get("added") or d.get("removed")):
                by["content"][el.kind] += 1
    for el in scene.list_edges():
        d = el.get("change")
        if d:
            by["relations"][d["status"]] += 1
    out = {k: dict(sorted(v.items())) for k, v in sorted(by.items())}
    out["files_changed"] = (scene.model.meta.get("change") or {}).get("files_changed", 0)
    return out
