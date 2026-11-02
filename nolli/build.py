"""Building models: extracting a commit, merging two commits' models into the model of the
change between them, and what is derived from a model (the review's diagram and brief, the
files a model's directory holds). Models are kept in a ModelStore and built from a Sources
(nolli/store.py). Used by the command line (``nolli/cli.py``) and the service
(``nolli/viewer/hosted.py``).

The steps that take time (extract_commit(), diff_commits()) take paths and models and return a
model, so a caller can run them elsewhere (``run``: the service runs them in processes of their
own); the store and the sources are used only where fetch_change_model() is called.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import match
from .extractors import Context, all_extractors, language_names
from .mermaid import render
from .model import Model, address
from .progress import Progress, Quiet
from .scene import SceneState, compute, scene
from .store import Change, Commit, ModelKey, ModelStore, Sources

BUDGET = 34  # boxes in a view, unless a model says otherwise (meta["budget"])
WORKING_NOTES = ("py-imports", "js-imports", "py-aliases", "ts-exports")  # extractors' state between passes, not kept

Run = Callable[..., Any]  # run(fn, *args, progress=…): fn(*args, progress=…), here or elsewhere


def run_here(fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
    return fn(*args, progress=progress)


def tracked_files(repo: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
        files = [Path(p.decode("utf-8", "surrogateescape")) for p in out.split(b"\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(repo) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [f for f in files if (repo / f).is_file()]


def git_head(repo: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def extract(repo: Path, config: dict | None = None, progress: Progress | None = None) -> tuple[Model, Context]:
    """The model of the files under ``repo`` as they are (a working tree, or a checkout)."""
    m = Model()
    progress = progress or Quiet()
    ctx = Context(repo=repo, model=m, files=tracked_files(repo), config=config or {}, progress=progress)
    timings = {}
    for name, fn in all_extractors():
        t0 = time.time()
        with progress.step("file tree" if name == "fs" else name.replace("-", " ")):
            fn(ctx)
        timings[name] = round(time.time() - t0, 3)
    t0 = time.time()
    with progress.step("matching references"):
        match.run_all(ctx)
    timings["match"] = round(time.time() - t0, 3)
    head = git_head(repo)
    m.meta = {"repo": str(repo), "commit": head[:7] if head else None, "files": len(ctx.files), "languages": language_names(),
              "timings": timings, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "notes": {k: v for k, v in ctx.notes.items() if k not in WORKING_NOTES}, "nolli": version()}
    return m, ctx


def extract_commit(repo: Path, sha: str, name: str, progress: Progress) -> Model:
    """The model of commit ``sha`` of the git repository ``repo`` (its files, not a working
    tree), its root named ``name``."""
    from .diff import materialise

    with tempfile.TemporaryDirectory(prefix="nolli-commit-") as tmp:
        with progress.step("checking out the commit"):
            materialise(repo, sha, Path(tmp))
        m, _ctx = extract(Path(tmp), progress=progress)
    if m.has(address.REPO_ROOT):  # the root is the temporary directory
        m.entities[address.REPO_ROOT].name = name
    m.meta.update({"repo": name, "commit": sha[:7]})
    return m


def diff_commits(repo: Path, base: Model, head: Model, base_sha: str, head_sha: str, progress: Progress) -> Model:
    """The model of the change from ``base_sha`` to ``head_sha``: their models merged, with what
    git says changed in each file (nolli/diff.py)."""
    from .diff import diff_models

    with progress.step("comparing the sides"):
        return diff_models(repo, base, head, base_sha, head_sha)


def is_fresh(m: Model) -> bool:
    """Built by this version of nolli (an older model is built again when it is asked for)."""
    return m.meta.get("nolli") == version()


def fetch_commit_model(store: ModelStore, clone: Path, commit: Commit, *, run: Run = run_here,
                       progress: Progress | None = None) -> Model:
    """The model of one commit: from the store, or extracted from ``clone`` (which has the
    commit) and kept there."""
    progress = progress or Quiet()
    m = store.load_model(commit)
    if m is not None and is_fresh(m):
        return m
    name = commit.repo.url.rstrip("/").split("/")[-1]
    m = run(extract_commit, clone, commit.sha, name, progress=progress)
    store.save_model(commit, m)
    return m


def fetch_change_model(store: ModelStore, sources: Sources, change: Change, *, about: dict | None = None,
                       run: Run = run_here, progress: Progress | None = None, rebuild: bool = False) -> Model:
    """The model of a change: from the store, or merged from its two commits' models and git's
    diff between them, and kept there. ``about``: what the model says about the change when
    it is built (``pr``, ``title``, ``description``; not part of its key), and ``budget``."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(change)
    if m is not None and is_fresh(m):
        return m
    with sources.open_commits(change.repo, change.base, change.head) as clone:
        base, head = (fetch_commit_model(store, clone, c, run=run, progress=progress.under(side))
                      for c, side in zip(change.list_sides(), ("base", "head"), strict=True))
        m = run(diff_commits, clone, base, head, change.base, change.head, progress=progress)
    about = dict(about or {})
    if about.get("budget"):
        m.meta["budget"] = about.pop("budget")
    m.meta["diff"]["remote"] = change.repo.format_remote()
    m.meta["diff"].update({k: v for k, v in about.items() if v})
    m.meta["nolli"] = version()
    with progress.step("keeping the model"):
        store.save_model(change, m)
    return m


def fetch_whole_model(store: ModelStore, sources: Sources, commit: Commit, *, budget: int | None = None,
                      run: Run = run_here, progress: Progress | None = None, rebuild: bool = False) -> Model:
    """The model of a repository at one commit, as the viewer shows a repository as a whole."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m) and (budget is None or m.meta.get("budget", BUDGET) == budget):
        return m
    with sources.open_commits(commit.repo, commit.sha) as clone:
        name = commit.repo.url.rstrip("/").split("/")[-1]
        m = run(extract_commit, clone, commit.sha, name, progress=progress)
    if budget:
        m.meta["budget"] = budget
    store.save_model(commit, m)
    return m


def version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("nolli-review")
    except PackageNotFoundError:
        return "unknown"


# -- derived from a model ------------------------------------------------------


def make_review_diagram(m: Model) -> str:
    """The diagram a review judges (Mermaid): every area that changed unfolded. The same for
    the same model and version of nolli, so verdicts on its ids stay valid."""
    return render(scene(m, SceneState(budget=m.meta.get("budget", BUDGET), preset="changes")))


def make_review_brief(m: Model, diagram: str | None = None) -> str:
    """What a review agent gets (Markdown): the diagram, how to answer, the change's description."""
    from .review import review_markdown

    return review_markdown(m, diagram if diagram is not None else make_review_diagram(m), (m.meta.get("diff") or {}).get("description"))


def make_report(m: Model) -> dict:
    """What a model is, in numbers: its meta, counts, the default view's size, unresolved references."""
    st = SceneState(budget=m.meta.get("budget", BUDGET))
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


def write_dataset(key: ModelKey | None, m: Model, out: Path, progress: Progress | None = None) -> dict:
    """A model's directory: model.json and report.json, and for a change what a review agent
    reads, changes.json, review.mmd and review.md. Returns the report.
    TODO: only model.json; the rest is derived and can be made when asked for."""
    from .changes import change_items

    from .store import format_key

    progress = progress or Quiet()
    out.mkdir(parents=True, exist_ok=True)
    if key is not None:
        m.meta["key"] = format_key(key)
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
