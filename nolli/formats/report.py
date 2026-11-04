"""What a model is, in numbers (``report.json``): its meta, how many nodes and edges of each
kind, the default picture's size, the unresolved references, and for a change how many
things changed."""

from __future__ import annotations

from collections import defaultdict

from ..model.scene import Scene
from ..transformations.projection import DEFAULT_BUDGET, SceneState, project


def summarize(scene: Scene) -> dict:
    by_kind: dict[str, int] = defaultdict(int)
    for k in scene.model.kinds.values():
        by_kind[k] += 1
    rel: dict[str, int] = defaultdict(int)
    for e in scene.model.edges:
        rel[f"{e.kind}/{e.subkind}" if e.subkind else e.kind] += 1
    return {"entities": dict(sorted(by_kind.items())), "relations": dict(sorted(rel.items()))}


def make_report(scene: Scene) -> dict:
    meta = scene.model.meta
    budget = (meta.get("change") or {}).get("budget") or meta.get("budget") or DEFAULT_BUDGET
    p = project(scene, SceneState(budget=budget)).model.meta["projection"]
    report = {"meta": meta, "summary": summarize(scene),
              "view": {"visible": p["stats"]["nodes"], "edges": p["stats"]["edges"], "internal": p["internal"],
                       "hidden_relations": p["hidden_relations"], "unresolved": p["unresolved"]},
              "unresolved": sorted(x for x, k in scene.model.kinds.items() if k == "unresolved"),
              "notes": meta.get("notes", {})}
    if "base" in scene.sources:
        from ..transformations.merge import count_changes

        report["diff"] = count_changes(scene)
    return report
