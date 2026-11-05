"""nolli as a library: what the command line (``nolli/cli.py``), the CI comment and the server
(``nolli/server/``) work with. ``Datasets`` is a store of models, where their commits come
from, and the overlays nolli shows on them; it opens a stored model as a scene (a commit, or
a change merged from its two commits), draws pictures of it, keeps and checks a review's
verdicts, and builds what is missing.

Below it: the model and its scenes (``nolli/model/``), the store (``nolli/store/``), where
commits come from (``nolli/sources/``), extraction (``nolli/extractors/``, run by
``nolli/build.py``), the transformations (``nolli/transformations/``), the review
(``nolli/review/``) and the formats (``nolli/formats/``), which take a scene.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .build import Run, extract, extract_commit, fetch_change, fetch_whole_model, is_fresh, list_content, run_here, version
from .model import Key, Model
from .model.keys import ChangeKey, CommitKey, ModelKey, Repo
from .model.scene import Overlay, Scene
from .progress import Progress, Quiet
from .review.changes import detail_box, find_review_cut, list_changes
from .review import ci
from .review.verdicts import (  # noqa: F401 (VERDICTS: for callers)
    VERDICTS, check_verdicts, describe_verdicts, find_diagram_ids, find_provenance, parse_verdicts, read_verdicts, save_verdicts,
)
from .sources import FetchError, Sources, github
from .sources.git import GitError, find_commit, find_head, find_remote
from .sources.git import ChangeError, ChangeSpec, find_toplevel
from .sources.git import resolve as resolve_spec, whole as resolve_whole
from .store import ModelStore
from .store.directory import write_change, write_commit
from .store.maps import MapStore, MemoryMaps
from .scenes import merge_built, open_built, open_change_scene, open_commit_scene
from .transformations.merge import is_change
from .formats.mermaid import list_ids, render
from .formats.viewer import export_scene
from .formats.report import make_report
from .review.brief import make_brief
from . import transformations
from .transformations import projection
from .transformations.projection import DEFAULT_BUDGET, ProjectionSpec

class Datasets:
    def __init__(self, store: ModelStore, sources: Sources | None = None) -> None:
        self.store, self.sources = store, sources

    # -- what is kept ------------------------------------------------------------

    def find_key(self, name: str) -> ModelKey | None:
        return self.store.find_key(name)

    def list_names(self) -> list[str]:
        return self.store.list_names()

    def open_scene(self, key: ModelKey) -> Scene | None:
        """The scene of a stored model: a change merged from its commits, or one commit. Raises
        NotAllowed as the store does."""
        return open_change_scene(self.store, key) if isinstance(key, ChangeKey) else open_commit_scene(self.store, key)

    def is_built(self, key: ModelKey) -> bool:
        """Kept, and built by this version of nolli."""
        return is_fresh(self.store.load_change(key) if isinstance(key, ChangeKey) else self.store.load_model(key))

    def build(self, key: ModelKey, *, about: dict | None = None, run: Run = run_here, progress: Progress | None = None,
              rebuild: bool = False) -> None:
        """Build what ``key`` names from ``sources`` unless a fresh one is kept. ``about``: what a
        change says about itself (``pr``, ``title``, ``description``, ``budget``)."""
        if self.sources is None:
            raise ValueError("no sources to build from")
        if isinstance(key, ChangeKey):
            fetch_change(self.store, self.sources, key, about=about, run=run, progress=progress, rebuild=rebuild)
        else:
            fetch_whole_model(self.store, self.sources, key, run=run, progress=progress, rebuild=rebuild)

    # -- pictures ------------------------------------------------------------------

    def draw(self, scene: Scene, state: ProjectionSpec) -> Scene:
        """The picture ``state`` asks for; a change's "changes" preset unfolds what it changed."""
        if state.preset == "changes" and state.focus is None and is_change(scene):
            state.focus = find_review_cut(scene)
        return transformations.apply(scene, projection.fold(state))

    def draw_json(self, scene: Scene, state: ProjectionSpec) -> dict:
        """The picture as the viewer reads it (``nolli/formats/viewer.py``)."""
        return export_scene(self.draw(scene, state))

    def draw_mermaid(self, scene: Scene, state: ProjectionSpec, fade: bool = True) -> tuple[str, dict[str, str]]:
        """The picture as Mermaid, and each Mermaid node id to the box it is."""
        picture = self.draw(scene, state)
        return render(picture, fade=fade), list_ids(picture)

    def list_changes(self, scene: Scene) -> list[dict]:
        """A change's numbered changes ([] for a commit)."""
        return list_changes(scene) if is_change(scene) else []

    def detail_box(self, scene: Scene, x: str) -> dict | None:
        """Everything about one box of a change (None for a commit, or no such box)."""
        return detail_box(scene, x) if is_change(scene) else None

    def describe_verdicts(self, scene: Scene) -> list[dict]:
        """A change's kept verdicts with what the viewer says about each."""
        return describe_verdicts(scene, self.load_verdicts(scene)) if is_change(scene) else []

    def read_verdicts(self, scene: Scene, text: str) -> list[dict]:
        """A verdicts.json on the change's review diagram ids, read (ValueError if it is bad)."""
        return read_verdicts(text, find_diagram_ids(scene))

    def read_ci_results(self, comments: list) -> list[dict]:
        """What nolli's CI left in a pull request's comments (GitHub's issue comments)."""
        return ci.results(comments)

    def draw_review(self, scene: Scene) -> Scene:
        """The change as its review diagram draws it: every area that changed in source code
        unfolded. The same for the same change and version of nolli, so verdicts on its ids
        stay valid."""
        budget = (scene.model.meta.get("change") or {}).get("budget") or DEFAULT_BUDGET
        spec = ProjectionSpec(budget=budget, preset="changes", focus=find_review_cut(scene))
        return transformations.apply(scene, projection.fold(spec))

    def make_review_diagram(self, scene: Scene) -> str:
        """The review diagram (``review.mmd``)."""
        return render(self.draw_review(scene))

    def make_review_brief(self, scene: Scene, diagram: str | None = None) -> str:
        """What a review agent gets (``review.md``): the diagram, how to answer, the change's description."""
        return make_brief(scene, diagram if diagram is not None else self.make_review_diagram(scene),
                          (scene.model.meta.get("change") or {}).get("description"))

    def make_report(self, scene: Scene) -> dict:
        """What a model is, in numbers (``report.json``), with its default picture's size."""
        meta = scene.model.meta
        budget = (meta.get("change") or {}).get("budget") or meta.get("budget") or DEFAULT_BUDGET
        return make_report(scene, self.draw(scene, ProjectionSpec(budget=budget)))

    def make_ci_comment(self, scene: Scene, skipped: str | None = None) -> str:
        """The pull request comment of a CI run on the change (``nolli/review/ci.py``)."""
        return ci.comment(scene, self.draw_review(scene), self.load_verdicts(scene), skipped)

    # -- a review's verdicts ---------------------------------------------------------

    def load_verdicts(self, scene: Scene) -> list[dict]:
        """A change's kept verdicts, in their order."""
        return parse_verdicts(self.store.open_maps("verdicts").load(scene.model.key))

    def find_provenance(self, key: ModelKey) -> dict | None:
        """Who made a change's verdicts; None without verdicts."""
        return find_provenance(self.store, key)

    def check_verdicts(self, scene: Scene, text: str) -> tuple[list[dict], list[str]]:
        """verdicts.json checked against the change's review diagram: (verdicts read, problems)."""
        return check_verdicts(scene, self.make_review_diagram(scene), text)

    def keep_verdicts(self, key: ModelKey, verdicts: list[dict], by: dict) -> None:
        """Make ``verdicts`` (read, with their ``ends``) the change's, with where they came from."""
        save_verdicts(self.store, key, verdicts, by)

    def recheck_verdicts(self, scene: Scene) -> tuple[list[dict] | None, list[str]]:
        """The kept verdicts checked again against the review diagram (None without verdicts)."""
        kept = self.load_verdicts(scene)
        return self.check_verdicts(scene, json.dumps(kept)) if kept else (None, [])


# -- from what a user names to datasets --------------------------------------------------


@dataclass
class Asked:
    """What a user named in a local repository, resolved: the repository, the model's key, and
    what it says about itself (``spec``: title, description; ``about``: also the PR)."""
    repo: Path
    key: ModelKey
    spec: ChangeSpec
    about: dict


BUILD_ERRORS = (ChangeError, FetchError, GitError)  # what building from a local repository may raise
is_ci_key, fetch_ci_result = ci.is_key, ci.fetch  # a CI run's result, by the key its PR comment gives


def find_repo(repo: Path) -> Repo:
    """A local repository's key: its GitHub remote if it has one (the same commits there are
    the same models), else its path."""
    remote = find_remote(repo)
    found = Repo.from_remote(remote) if remote else None
    return found if found and found.format_remote() else Repo(str(repo.resolve()))


def resolve_change(path: Path, change: str | None, whole: bool = False) -> Asked:
    """What ``change`` names in the git repository around ``path``: a change (the current branch
    by default, ``A..B``, ``A...B``, a GitHub PR's URL or ``#<n>``), or with ``whole`` one commit
    as a whole. Raises ChangeError."""
    repo = find_toplevel(path.resolve())
    host: dict = {}
    if whole:
        spec = resolve_whole(repo, change or "HEAD")
    elif github.is_pull_request(change):
        spec, host = github.resolve(repo, change)
    else:
        spec = resolve_spec(repo, change)
    key_repo = Repo.from_remote(host["remote"]) if host.get("remote") else find_repo(repo)
    key: ModelKey = key_repo.make_change(spec.base, spec.head) if spec.is_diff else key_repo.make_commit(spec.head)
    return Asked(repo, key, spec, {"pr": host.get("pr"), "title": spec.title, "description": spec.description})


def write_extras(datasets: "Datasets", scene: Scene, out: Path) -> dict:
    """What a dataset directory written on its own gets beside the model: report.json, and for a
    change what a review agent reads (changes.json, review.mmd, review.md). Returns the report."""
    report = datasets.make_report(scene)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    if is_change(scene):
        (out / "changes.json").write_text(json.dumps(list_changes(scene), indent=1))
        mmd = datasets.make_review_diagram(scene)
        (out / "review.mmd").write_text(mmd)
        (out / "review.md").write_text(datasets.make_review_brief(scene, mmd))
    return report


def extract_dataset(repo: Path, out: Path, budget: int | None = None) -> dict:
    """The model of the files under ``repo`` as they are, written to the directory ``out`` (a
    dataset directory, as ``DirectoryStore`` keeps one) with its extras; the report."""
    key = find_repo(repo).make_commit(find_head(repo) or "worktree")
    m, maps = extract(repo, key=key.format_key())
    if budget:
        m.meta["budget"] = budget
    out.mkdir(parents=True, exist_ok=True)
    write_commit(out, key, m, maps)
    return write_extras(Datasets(MemoryMaps()), open_built(m, maps), out)


def diff_dataset(repo: Path, base_rev: str, head_rev: str, out: Path, about: dict) -> tuple[dict, dict]:
    """The change between two revisions of the git repository ``repo``, built here and written
    to the directory ``out`` with its extras; what the change says about itself, and the report."""
    base, head = find_commit(repo, base_rev), find_commit(repo, head_rev)
    key = find_repo(repo).make_change(base, head)
    sides = {}
    for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
        m, maps = extract_commit(repo, commit.sha, repo.name, Quiet())
        m.key = commit.format_key()
        sides[side] = (m, maps)
    content = list_content(repo, base, head, Quiet())
    about = {k: v for k, v in about.items() if v}
    about.update({"base": base, "head": head, "remote": key.repo.format_remote(), "nolli": version()})
    out.mkdir(parents=True, exist_ok=True)
    write_change(out, key, about, sides, {"content": content})
    scene = merge_built(sides["base"], sides["head"], key.format_key(), about, content)
    return about, write_extras(Datasets(MemoryMaps()), scene, out)
