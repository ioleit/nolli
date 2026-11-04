"""Reviews of a server's models: where a model's verdicts came from, and review agent runs
started from the viewer ("Review structural changes").

A change's review is its ``verdicts`` map (nolli/overlays/verdicts.py) and who made them, the
map's ``about``: ``{"by": "agent" | "ci" | "upload", "at": <ISO time>, ...}``, with the model, time and cost
of an agent run here, or the link to the CI comment they were read from. A pull request's
model can take its verdicts from nolli's CI (from_ci(), given the PR's comments; the server
asks GitHub for them). A run (nolli/reviewer.py) works in a directory of its own under the
server's work directory (its transcript stays there until the next run) and replaces the
verdicts only when its own pass nolli's check, so the ones shown stay until then. Runs live
in memory: a server that restarts forgets runs under way, not verdicts.
"""

from __future__ import annotations

import contextvars
import json
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from nolli.keys import ChangeKey, ModelKey
from nolli.overlays.verdicts import find_provenance as provenance, save_verdicts  # noqa: F401 (the server's)
from nolli.scene import Scene
from nolli.sources import Sources
from nolli.store import ModelStore

RUNS = threading.BoundedSemaphore(2)  # agent runs at the same time; the rest wait their turn
STEPS_KEPT = 6  # the latest steps a page shows under the one under way


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ci_pull(scene: Scene) -> tuple[str, str, int] | None:
    """The GitHub pull request a change was built from: (owner, repo, number), or None."""
    d = scene.model.meta.get("change") or {}
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", d.get("remote") or "")
    return (match.group(1), match.group(2), int(d["pr"])) if match and d.get("pr") else None


def from_ci(store: ModelStore, key: ModelKey, scene: Scene, comments: list | dict) -> dict:
    """What nolli's CI left in a pull request's comments (GitHub's answer) for the change's head
    commit: ``{"found": "verdicts" | "no-review" | "earlier" | None, "applies": True, ...}``. Its
    verdicts, checked against this change's diagram, become the change's, unless it has some
    already. A result of another format (an earlier nolli) is not read."""
    from nolli.ci import results
    from nolli.export.brief import make_review_diagram
    from nolli.overlays.verdicts import check_verdicts

    head = scene.model.meta["change"]["head"]
    found = results(comments if isinstance(comments, list) else [])
    info: dict = {"found": None, "applies": True}
    mine = [r for r in found if r["head"] == head]
    if not mine:
        if found:
            info.update(found="earlier", head=found[0]["head"][:7], link=found[0]["link"])
        return info
    r = mine[0]
    info.update(link=r["link"], at=r["at"], nolli=r.get("nolli"), found="verdicts" if r.get("verdicts") else "no-review")
    if r.get("verdicts") and provenance(store, key) is None:
        ok, errors = check_verdicts(scene, make_review_diagram(scene), json.dumps(r["verdicts"]))
        info["dropped"] = len(errors)
        if ok:
            save_verdicts(store, key, ok, {"by": "ci", "link": r["link"], "at": r["at"], "nolli": r.get("nolli"), "dropped": len(errors)})
    return info


@dataclass
class Job:
    state: str = "queued"  # queued, running, done, failed, cancelled
    step: str = ""
    steps: list[str] = field(default_factory=list)
    error: str = ""
    t0: float = field(default_factory=time.time)
    started: float = 0.0
    ended: float = 0.0
    turns: int = 0
    cost: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)

    def status(self) -> dict:
        end = self.ended or time.time()
        return {"state": self.state, "step": self.step, "steps": self.steps[-STEPS_KEPT:], "error": self.error,
                "seconds": round(end - (self.started or self.t0)) if self.started else 0,
                "waiting": round(end - self.t0) if not self.started else 0, "turns": self.turns, "cost": round(self.cost, 2)}


class Reviews:
    """The review runs of one server, by model name."""

    def __init__(self, reviewer=None, work: Path | None = None):
        """``reviewer``: what runs a review (nolli.reviewer.Reviewer, the default, or a service's
        own). ``work``: where runs keep their files (default: a temporary directory)."""
        if reviewer is None:
            from nolli.reviewer import Reviewer

            reviewer = Reviewer()
        self.reviewer = reviewer
        self.work = work or Path(tempfile.gettempdir()) / "nolli-reviews"
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def job(self, name: str) -> Job | None:
        return self.jobs.get(name)

    def start(self, name: str, store: ModelStore, key: ChangeKey, sources: Sources) -> Job:
        """The run for ``name``: the one waiting or under way, or a new one. It reads the model
        from ``store``, and the change's commits from ``sources`` when its turn comes. It runs in
        the context of the request that started it (contextvars), as a store may need."""
        with self.lock:
            job = self.jobs.get(name)
            if job and job.state in ("queued", "running"):
                return job
            job = self.jobs[name] = Job()
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(self._run, job, name, store, key, sources), daemon=True).start()
        return job

    def cancel(self, name: str) -> Job | None:
        job = self.jobs.get(name)
        if job and job.state in ("queued", "running"):
            job.cancel.set()
            if job.state == "queued":  # nothing to stop yet; its turn will see the flag
                job.state, job.ended = "cancelled", time.time()
        return job

    def _run(self, job: Job, name: str, store: ModelStore, key: ChangeKey, sources: Sources) -> None:
        from nolli import reviewer
        from nolli.build import open_change_scene
        from nolli.export.brief import make_review_brief, make_review_diagram
        from nolli.overlays.verdicts import check_verdicts

        def on_step(s: str, run: reviewer.Run) -> None:
            if s != job.step:
                if job.step and job.step != "thinking":
                    job.steps.append(job.step)
                job.step = s
            job.turns, job.cost = run.turns, run.cost

        with RUNS:
            if job.cancel.is_set():
                job.state, job.ended = "cancelled", job.ended or time.time()
                return
            job.state, job.started = "running", time.time()
            work = self.work / name
            shutil.rmtree(work, ignore_errors=True)
            try:
                scene = open_change_scene(store, key)
                if scene is None:
                    raise ValueError(f"no model of {name}")
                out = work / "out"  # what the reviewer writes; its brief, as it read it, beside
                out.mkdir(parents=True)
                diagram = make_review_diagram(scene)
                brief = make_review_brief(scene, diagram)
                (work / "review.md").write_text(brief)

                def check(text: str):
                    return check_verdicts(scene, diagram, text)

                job.step = "fetching the change's commits"
                with sources.open_commits(key.repo, key.base, key.head) as repo:
                    run = self.reviewer.review(brief, key.head, check, repo, out, on_step, job.cancel)
                verdicts, _errors = check((out / "verdicts.json").read_text())
                save_verdicts(store, key, verdicts, {"by": "agent", "model": run.model, "seconds": run.seconds,
                                                     "turns": run.turns, "cost": round(run.cost, 2)})
                job.state, job.turns, job.cost = "done", run.turns, run.cost
            except reviewer.Cancelled:
                job.state = "cancelled"
            except reviewer.Failed as exc:
                job.state, job.error = "failed", str(exc)
            except Exception as exc:  # noqa: BLE001  # the API, the network, git: shown on the page
                job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                job.ended = time.time()  # the run's transcript stays in its work directory, until the next run
