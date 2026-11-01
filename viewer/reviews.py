"""Reviews of a server's datasets: where a dataset's verdicts came from, and review agent runs
started from the viewer ("Review structural changes").

A dataset's verdicts are its ``verdicts.json``; ``review.json`` next to it says who made them
(store(), provenance()). A pull request's dataset can take its verdicts from nolli's CI
(from_ci(), given the PR's comments; the server asks GitHub for them). ``review.json``:
``{"by": "agent" | "ci" | "upload", "at": <ISO time>, ...}``, with the model, time and cost
of an agent run here, or the link to the CI comment they were read from. A run
(nolli/reviewer.py) works in ``<dataset>/.review`` (its transcript stays there) and replaces the verdicts only when its own
pass nolli's check, so the ones shown stay until then. Runs live in memory: a server that
restarts forgets runs under way, not verdicts.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

RUNS = threading.BoundedSemaphore(2)  # agent runs at the same time; the rest wait their turn
STEPS_KEPT = 6  # the latest steps a page shows under the one under way


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def provenance(ds: Path) -> dict | None:
    """review.json, or {"by": "unknown"} for verdicts from before it existed; None without verdicts."""
    if not (ds / "verdicts.json").is_file():
        return None
    try:
        return json.loads((ds / "review.json").read_text())
    except (OSError, ValueError):
        return {"by": "unknown"}


def store(ds: Path, verdicts: list[dict], by: dict) -> None:
    """Make ``verdicts`` (checked) the dataset's, with where they came from."""
    (ds / "verdicts.json").write_text(json.dumps([{k: v[k] for k in ("target", "verdict", "reason")} for v in verdicts], indent=1))
    (ds / "review.json").write_text(json.dumps({**by, "at": by.get("at") or now()}, indent=1))


def ci_pull(ds: Path) -> tuple[str, str, int] | None:
    """The GitHub pull request a dataset was built from: (owner, repo, number), or None."""
    try:
        d = json.loads((ds / "report.json").read_text())["meta"].get("diff") or {}
    except (OSError, ValueError, KeyError):
        return None
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", d.get("remote") or "")
    return (m.group(1), m.group(2), int(d["pr"])) if m and d.get("pr") else None


def from_ci(ds: Path, comments: list | dict) -> dict:
    """What nolli's CI left in a pull request's comments (GitHub's answer) for the dataset's head
    commit: ``{"found": "verdicts" | "no-review" | "earlier" | None, "applies": True, ...}``. Its
    verdicts, translated from an earlier nolli's ids and checked against this dataset's
    diagram, become the dataset's, unless it has some already."""
    from nolli.ci import results, upgrade
    from nolli.model import Model
    from nolli.review import check_verdicts

    head = json.loads((ds / "report.json").read_text())["meta"]["diff"]["head"]
    found = results(comments if isinstance(comments, list) else [])
    info: dict = {"found": None, "applies": True}
    mine = [r for r in found if r["head"] == head]
    if not mine:
        if found:
            info.update(found="earlier", head=found[0]["head"][:7], link=found[0]["link"])
        return info
    r = mine[0]
    info.update(link=r["link"], at=r["at"], nolli=r.get("nolli"), found="verdicts" if r.get("verdicts") else "no-review")
    if r.get("verdicts") and provenance(ds) is None:
        given = upgrade(r["verdicts"], r.get("nolli"))
        ok, errors = check_verdicts(Model.load(ds / "model.json"), (ds / "review.mmd").read_text(), json.dumps(given))
        info["dropped"] = len(errors)
        if ok:
            store(ds, ok, {"by": "ci", "link": r["link"], "at": r["at"], "nolli": r.get("nolli"), "dropped": len(errors)})
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
    """The review runs of one server, by dataset name."""

    def __init__(self, reviewer=None):
        """``reviewer``: what runs a review (nolli.reviewer.Reviewer, the default, or a service's own)."""
        if reviewer is None:
            from nolli.reviewer import Reviewer

            reviewer = Reviewer()
        self.reviewer = reviewer
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def job(self, name: str) -> Job | None:
        return self.jobs.get(name)

    def start(self, name: str, ds: Path, repo: Callable[[], Path]) -> Job:
        """The run for ``name``: the one waiting or under way, or a new one. ``repo`` gives the
        repository with the change's commits, when the run's turn comes."""
        with self.lock:
            job = self.jobs.get(name)
            if job and job.state in ("queued", "running"):
                return job
            job = self.jobs[name] = Job()
        threading.Thread(target=self._run, args=(job, ds, repo), daemon=True).start()
        return job

    def cancel(self, name: str) -> Job | None:
        job = self.jobs.get(name)
        if job and job.state in ("queued", "running"):
            job.cancel.set()
            if job.state == "queued":  # nothing to stop yet; its turn will see the flag
                job.state, job.ended = "cancelled", time.time()
        return job

    def _run(self, job: Job, ds: Path, repo: Callable[[], Path]) -> None:
        from nolli import reviewer

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
            work = ds / ".review"
            shutil.rmtree(work, ignore_errors=True)
            try:
                job.step = "fetching the change's commits"
                run = self.reviewer.review(ds, repo(), work, on_step, job.cancel)
                verdicts = json.loads((work / "verdicts.json").read_text())
                store(ds, verdicts, {"by": "agent", "model": run.model, "seconds": run.seconds, "turns": run.turns,
                                     "cost": round(run.cost, 2)})
                job.state, job.turns, job.cost = "done", run.turns, run.cost
            except reviewer.Cancelled:
                job.state = "cancelled"
            except reviewer.Failed as exc:
                job.state, job.error = "failed", str(exc)
            except Exception as exc:  # noqa: BLE001  # the API, the network, git: shown on the page
                job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                job.ended = time.time()  # the run's transcript stays in .review, until the next run
