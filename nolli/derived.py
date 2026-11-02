"""What is made from a model: the diagram a review judges and the brief a review agent gets, a
report in numbers, and the files of a model's directory (DirectoryStore, nolli diff -o).
Depends on the model and how it is drawn, not on where models are kept or how they are built.
"""

from __future__ import annotations

import json
from pathlib import Path

from .mermaid import render
from .model import Model
from .progress import Progress, Quiet
from .scene import DEFAULT_BUDGET, SceneState, compute, scene


def make_review_diagram(m: Model) -> str:
    """The diagram a review judges (Mermaid): every area that changed unfolded. The same for
    the same model and version of nolli, so verdicts on its ids stay valid."""
    return render(scene(m, SceneState(budget=m.meta.get("budget", DEFAULT_BUDGET), preset="changes")))


def make_review_brief(m: Model, diagram: str | None = None) -> str:
    """What a review agent gets (Markdown): the diagram, how to answer, the change's description."""
    from .review import review_markdown

    return review_markdown(m, diagram if diagram is not None else make_review_diagram(m), (m.meta.get("diff") or {}).get("description"))


def make_report(m: Model) -> dict:
    """What a model is, in numbers: its meta, counts, the default view's size, unresolved references."""
    st = SceneState(budget=m.meta.get("budget", DEFAULT_BUDGET))
    _expanded, _hidden, visible, rolled, _utilities = compute(m, st)
    report = {
        "meta": m.meta,
        "summary": m.summary(),
        "view": {"visible": len(visible), "edges": len(rolled.edges), "internal": rolled.internal,
                 "hidden_relations": rolled.hidden_relations, "unresolved": rolled.unresolved},
        "unresolved": sorted(e.id for e in m.entities.values() if e.kind == "unresolved"),
        "notes": m.meta.get("notes", {}),
    }
    if (m.meta.get("diff") or {}).get("counts"):
        report["diff"] = m.meta["diff"]["counts"]
    return report


def write_dataset(m: Model, out: Path, progress: Progress | None = None) -> dict:
    """A model's directory: model.json and report.json, and for a change what a review agent
    reads, changes.json, review.mmd and review.md. Returns the report.
    TODO: only model.json; the rest is made when asked for."""
    from .changes import change_items

    progress = progress or Quiet()
    out.mkdir(parents=True, exist_ok=True)
    with progress.step("writing model.json"):
        m.save(out / "model.json")
    with progress.step("the default cut"):
        report = make_report(m)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    if m.meta.get("diff"):
        with progress.step("the review's diagram"):
            (out / "changes.json").write_text(json.dumps(change_items(m), indent=1))
            mmd = make_review_diagram(m)
            (out / "review.mmd").write_text(mmd)
            (out / "review.md").write_text(make_review_brief(m, mmd))
    return report
