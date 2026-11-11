"""nolli as a library: what the command line (``nolli/cli.py``) and the server
(``nolli/server/``) work with. ``Datasets`` is a store of models, where their commits come
from, and the overlays nolli shows on them; it opens a stored model as a scene (a commit, or
a change merged from its two commits), draws projections of it, keeps and checks a review's
verdicts, and builds what is missing.

Below it: the model and its scenes (``nolli/model/``), the store (``nolli/store/``), where
commits come from (``nolli/logic/sources/``), extraction (``nolli/logic/extractors/``, run by
``nolli/build.py``), the transformations (``nolli/logic/transformations/``), the review
(``nolli/review/``) and the formats (``nolli/logic/formats/``), which take a scene.
"""

from __future__ import annotations

import json
from pathlib import Path

import nolli.logic.formats.mermaid
import nolli.logic.formats.report
import nolli.logic.sources
import nolli.logic.sources.git
import nolli.logic.sources.github
import nolli.logic.transformations
import nolli.logic.transformations.merge
import nolli.logic.transformations.projection
import nolli.model.keys
import nolli.model.scene
import nolli.review.brief
import nolli.review.changes
import nolli.review.verdicts
import nolli.store

# CALLERS (temp, not to be merged):
#   cli.py:369 cmd_check — nolli agent check
VERDICTS = nolli.review.verdicts.VERDICTS  # for callers


# CALLERS (temp, not to be merged):
#   cli.py:110 Built — nolli build, nolli view --rebuild, nolli agent brief
#   cli.py:144 build() — nolli build, nolli view --rebuild, nolli agent brief
#   cli.py:353 cmd_check — nolli agent check
#   serve.py:336 Site.find_built — GET /?t=, GET /<owner>/<repo>/…
#   serve.py:364 Site.run_job — build job (page routes, GET /api/job)
#   serve.py:486 Services.datasets — every GET /api/*, POST /api/review[/cancel], POST /api/verdicts
#   reviews.py:98 Reviews._run — review job (POST /api/review)
#   datasets.py: write_dataset, extract_directory (Datasets(MemoryMaps()) for write_extras)
#   tests: test_store.py, test_server.py (monkeypatches build)
class Datasets:
    def __init__(self, store: nolli.store.ModelStore) -> None:
        self.store = store

    # -- Projections ------------------------------------------------------------------

    # CALLERS (temp, not to be merged):
    #   datasets.py only: draw_json, draw_mermaid, make_report
    def draw(self, scene: nolli.model.scene.Scene, state: nolli.logic.transformations.projection.ProjectionSpec) -> nolli.model.scene.Scene:
        """The projection ``state`` asks for; a change's "changes" preset unfolds what it changed."""
        if state.preset == "changes" and state.focus is None and nolli.logic.transformations.merge.is_change(scene):
            state.focus = nolli.review.changes.find_review_cut(scene)
        return nolli.logic.transformations.apply(scene, nolli.logic.transformations.projection.fold(state))

    # CALLERS (temp, not to be merged):
    #   serve.py:639 answer_api — GET /api/changes
    #   tests: test_store.py
    def list_changes(self, scene: nolli.model.scene.Scene) -> list[dict]:
        """A change's numbered changes ([] for a commit)."""
        return nolli.review.changes.list_changes(scene) if nolli.logic.transformations.merge.is_change(scene) else []

    # CALLERS (temp, not to be merged):
    #   serve.py:639 answer_api — GET /api/changes
    def describe_verdicts(self, scene: nolli.model.scene.Scene) -> list[dict]:
        """A change's kept verdicts with what the viewer says about each."""
        return nolli.review.verdicts.describe_verdicts(scene, self.load_verdicts(scene)) if nolli.logic.transformations.merge.is_change(scene) else []

    # CALLERS (temp, not to be merged):
    #   serve.py:677 do_POST — POST /api/verdicts (view only)
    def read_verdicts(self, scene: nolli.model.scene.Scene, text: str) -> list[dict]:
        """A verdicts.json on the change's review diagram ids, read (ValueError if it is bad)."""
        return nolli.review.verdicts.read_verdicts(text, nolli.review.verdicts.find_diagram_ids(scene))

    # CALLERS (temp, not to be merged):
    #   datasets.py only: make_review_diagram
    def draw_review(self, scene: nolli.model.scene.Scene) -> nolli.model.scene.Scene:
        """The change as its review diagram draws it: every area that changed in source code
        unfolded. The same for the same change and version of nolli, so verdicts on its ids
        stay valid."""
        budget = (scene.model.meta.get("change") or {}).get("budget") or nolli.logic.transformations.projection.DEFAULT_BUDGET
        spec = nolli.logic.transformations.projection.ProjectionSpec(budget=budget, preset="changes", focus=nolli.review.changes.find_review_cut(scene))
        return nolli.logic.transformations.apply(scene, nolli.logic.transformations.projection.fold(spec))

    # CALLERS (temp, not to be merged):
    #   reviews.py:120 Reviews._run — review job (POST /api/review)
    #   datasets.py: make_review_brief, check_verdicts, write_extras (nolli build -o, nolli diff, nolli extract)
    #   tests: test_store.py
    def make_review_diagram(self, scene: nolli.model.scene.Scene) -> str:
        """The review diagram (``review.mmd``)."""
        return nolli.logic.formats.mermaid.render(self.draw_review(scene))

    # CALLERS (temp, not to be merged):
    #   cli.py:328 cmd_brief — nolli agent brief
    #   serve.py:644 answer_api — GET /api/brief
    #   reviews.py:121 Reviews._run — review job (POST /api/review)
    #   datasets.py: write_extras (nolli build -o, nolli diff, nolli extract)
    #   tests: test_store.py
    def make_review_brief(self, scene: nolli.model.scene.Scene, diagram: str | None = None) -> str:
        """What a review agent gets (``review.md``): the diagram, how to answer, the change's description."""
        return nolli.review.brief.make_brief(scene, diagram if diagram is not None else self.make_review_diagram(scene),
                          (scene.model.meta.get("change") or {}).get("description"))

    # CALLERS (temp, not to be merged):
    #   datasets.py only: write_extras (nolli build -o, nolli diff, nolli extract)
    def make_report(self, scene: nolli.model.scene.Scene) -> dict:
        """The ``report.json`` of a model, its counts and the size of its default projection."""
        meta = scene.model.meta
        budget = (meta.get("change") or {}).get("budget") or meta.get("budget") or nolli.logic.transformations.projection.DEFAULT_BUDGET
        return nolli.logic.formats.report.make_report(scene, self.draw(scene, nolli.logic.transformations.projection.ProjectionSpec(budget=budget)))

    # -- a review's verdicts ---------------------------------------------------------

    # CALLERS (temp, not to be merged):
    #   datasets.py only: describe_verdicts, recheck_verdicts
    def load_verdicts(self, scene: nolli.model.scene.Scene) -> list[dict]:
        """A change's kept verdicts, in their order."""
        return nolli.review.verdicts.parse_verdicts(self.store.open_maps("verdicts").load(scene.model.key.format_key()))

    # CALLERS (temp, not to be merged):
    #   cli.py:333 cmd_brief — nolli agent brief
    #   serve.py:603 Handler.review_status — GET /api/review, POST /api/review, POST /api/review/cancel
    def find_provenance(self, key: nolli.model.keys.ModelKey) -> dict | None:
        """Who made a change's verdicts; None without verdicts."""
        return nolli.review.verdicts.find_provenance(self.store, key)

    # CALLERS (temp, not to be merged):
    #   cli.py:366 cmd_check — nolli agent check
    #   reviews.py:125 Reviews._run.check — review job (POST /api/review)
    #   datasets.py: recheck_verdicts
    #   tests: test_store.py
    def check_verdicts(self, scene: nolli.model.scene.Scene, text: str) -> tuple[list[dict], list[str]]:
        """verdicts.json checked against the change's review diagram: (verdicts read, problems)."""
        return nolli.review.verdicts.check_verdicts(scene, self.make_review_diagram(scene), text)

    # CALLERS (temp, not to be merged):
    #   cli.py:373 cmd_check — nolli agent check
    #   serve.py:681 do_POST — POST /api/verdicts (view only)
    #   reviews.py:131 Reviews._run — review job (POST /api/review)
    #   tests: test_store.py
    def keep_verdicts(self, key: nolli.model.keys.ModelKey, kept: list[dict], by: dict) -> None:
        """Make ``kept`` (verdicts read, with their ``ends``) the change's, with where they came from."""
        nolli.review.verdicts.save_verdicts(self.store, key, kept, by)

    # CALLERS (temp, not to be merged):
    #   none: no caller anywhere (dead code)
    def recheck_verdicts(self, scene: nolli.model.scene.Scene) -> tuple[list[dict] | None, list[str]]:
        """The kept verdicts checked again against the review diagram (None without verdicts)."""
        kept = self.load_verdicts(scene)
        return self.check_verdicts(scene, json.dumps(kept)) if kept else (None, [])


# CALLERS (temp, not to be merged):
#   cli.py build_into, cmd_extract — nolli build -o, nolli extract, nolli diff
def write_extras(datasets: "Datasets", scene: nolli.model.scene.Scene, out: Path) -> dict:
    """What a dataset directory written on its own gets beside the model: report.json, and for a
    change what a review agent reads (changes.json, review.mmd, review.md). Returns the report."""
    report = datasets.make_report(scene)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    if nolli.logic.transformations.merge.is_change(scene):
        (out / "changes.json").write_text(json.dumps(nolli.review.changes.list_changes(scene), indent=1))
        mmd = datasets.make_review_diagram(scene)
        (out / "review.mmd").write_text(mmd)
        (out / "review.md").write_text(datasets.make_review_brief(scene, mmd))
    return report
