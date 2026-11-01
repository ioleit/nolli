"""Building a dataset: extracting a repository (or a commit, or a change between two) and
writing what the viewer and a review agent read. Used by the command line (``nolli/cli.py``)
and by the service (``nolli/viewer/hosted.py``); what the files are is in ``nolli/cli.py``.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import match
from .extractions import Extractions
from .extractors import Context, all_extractors, language_names
from .mermaid import render
from .model import Model, address
from .progress import Progress, Quiet
from .scene import ALL_ROLES, DEFAULT_ROLES, SceneState, compute, scene


def tracked_files(repo: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
        files = [Path(p.decode("utf-8", "surrogateescape")) for p in out.split(b"\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(repo) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [f for f in files if (repo / f).is_file()]


def git_head(repo: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def extract(repo: Path, config: dict | None = None, progress: Progress | None = None) -> tuple[Model, Context]:
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
    m.meta = {"repo": str(repo), "commit": git_head(repo), "files": len(ctx.files), "languages": language_names(), "timings": timings,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return m, ctx


def build_diff(repo: Path, base: str, head: str, out: Path, budget: int, pr: int | None = None, title: str | None = None,
               description: str | None = None, remote: str | None = None,
               progress: Progress | None = None, extractions: Extractions | None = None) -> tuple[Model, dict, dict]:
    """The dataset of a change: the merged model, changes.json, review.mmd and review.md. With
    ``extractions``, sides extracted before are reused (nolli/extractions.py)."""
    from .changes import change_items
    from .diff import diff
    from .review import review_markdown

    progress = progress or Quiet()
    out.mkdir(parents=True, exist_ok=True)
    m, summary = diff(repo, base, head, extract, progress, extractions)
    m.meta["nolli"] = version()
    if remote:
        m.meta["diff"]["remote"] = remote
    if pr:
        m.meta["diff"]["pr"] = pr
    if title:
        m.meta["diff"]["title"] = title
    report = write_dataset(m, out, budget, {}, {"diff": summary}, progress)
    with progress.step("the review's diagram"):
        (out / "changes.json").write_text(json.dumps(change_items(m), indent=1))
        mmd = render(scene(m, SceneState(budget=budget, preset="changes")))
        (out / "review.mmd").write_text(mmd)
        (out / "review.md").write_text(review_markdown(m, mmd, description))
    return m, summary, report


def build_commit(repo: Path, sha: str, out: Path, budget: int, progress: Progress | None = None,
                 extractions: Extractions | None = None) -> dict:
    """The dataset of one commit as a whole, extracted from the commit (not the working tree),
    or read from ``extractions`` if it was extracted before (nolli/extractions.py)."""
    import tempfile

    from .diff import materialise

    progress = progress or Quiet()
    out.mkdir(parents=True, exist_ok=True)
    hit = None
    if extractions and extractions.has(sha):
        with progress.step("reading an earlier extraction"):
            hit = extractions.get(sha)
    if hit:
        m, notes = hit
    else:
        with tempfile.TemporaryDirectory(prefix="nolli-commit-") as tmp:
            with progress.step("checking out the commit"):
                materialise(repo, sha, Path(tmp))
            m, ctx = extract(Path(tmp), progress=progress)
        notes = ctx.notes
        if extractions:
            extractions.put(sha, m, notes)
    if m.has(address.REPO_ROOT):  # the root is the temporary directory; name it after the repository
        m.entities[address.REPO_ROOT].name = repo.name
    m.meta.update({"repo": repo.name, "commit": sha[:7], "nolli": version()})
    return write_dataset(m, out, budget, notes, progress=progress)


def version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("nolli-review")
    except PackageNotFoundError:
        return "unknown"


def fresh(out: Path) -> bool:
    """A dataset built before by this version of nolli."""
    try:
        return json.loads((out / "report.json").read_text())["meta"].get("nolli") == version()
    except (OSError, ValueError, KeyError):
        return False


def write_dataset(m: Model, out: Path, budget: int, notes: dict, extra: dict | None = None,
                  progress: Progress | None = None) -> dict:
    """model.json, view.json, scene.json, default.mmd and report.json for a model."""
    progress = progress or Quiet()
    with progress.step("writing model.json"):
        m.save(out / "model.json")
    with progress.step("the default cut"):
        st = SceneState(budget=budget)
        expanded, _hidden, visible, rolled, _utilities = compute(m, st)
        view = {"budget": budget, "expanded": sorted(expanded), "visible": sorted(visible),
                "hidden_roles": [r for r in ALL_ROLES if r not in DEFAULT_ROLES]}
        (out / "view.json").write_text(json.dumps(view, indent=1))
        sc = scene(m, st)
        (out / "scene.json").write_text(json.dumps(sc, indent=1))
        (out / "rollup.json").unlink(missing_ok=True)  # replaced by scene.json
        (out / "default.mmd").write_text(render(sc))
    report = {
        "meta": m.meta,
        "summary": m.summary(),
        "view": {"visible": len(view["visible"]), "edges": len(rolled.edges), "internal": rolled.internal,
                 "hidden_relations": rolled.hidden_relations, "unresolved": rolled.unresolved},
        "unresolved": sorted(e.id for e in m.entities.values() if e.kind == "unresolved"),
        "notes": {k: v for k, v in notes.items() if k not in ("py-imports", "js-imports", "py-aliases", "ts-exports")},
        **(extra or {}),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return report
