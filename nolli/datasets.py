"""nolli as a library: what the command line (``nolli/cli.py``) and the server
(``nolli/server/``) work with. ``Datasets`` is a store of models, where their commits come
from, and the overlays nolli shows on them; it opens a stored model as a scene (a commit, or
a change merged from its two commits), draws projections of it, keeps and checks a review's
verdicts, and builds what is missing.

Below it: the model and its scenes (``nolli/model/``), the store (``nolli/store/``), where
commits come from (``nolli/sources/``), extraction (``nolli/extractors/``, run by
``nolli/build.py``), the transformations (``nolli/transformations/``), the review
(``nolli/review/``) and the formats (``nolli/formats/``), which take a scene.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import transformations
from .build import (
    Run,
    extract,
    extract_commit,
    fetch_change,
    fetch_whole_model,
    is_fresh,
    list_content,
    run_here,
    version,
)
from .formats.mermaid import list_ids, render
from .formats.report import make_report
from .formats.viewer import export_scene
from .model.keys import ChangeKey, CommitKey, ModelKey, Repo
from .model.model import Key, Model
from .model.scene import Overlay, Scene
from .progress import Progress, Quiet
from .review.brief import make_brief
from .review.changes import find_review_cut, list_changes
from .review.verdicts import (  # noqa: F401 (VERDICTS: for callers)
    VERDICTS,
    check_verdicts,
    describe_verdicts,
    find_diagram_ids,
    find_provenance,
    parse_verdicts,
    read_verdicts,
    save_verdicts,
)
from .scenes import (
    describe_change_meta,
    keep_change_scene,
    keep_commit_scene,
    merge_built,
    open_built,
    open_change_scene,
    open_commit_scene,
)
from .sources import FetchError, LocalRepository, Sources, github
from .sources.git import (
    WORKTREE,
    ChangeError,
    ChangeSpec,
    GitError,
    find_remote,
    find_toplevel,
    resolve_adds,
    resolve_own,
    resolve_range,
    resolve_snapshot,
)
from .store import ModelStore
from .store.directory import DirectoryStore, write_change, write_commit
from .store.maps import MapStore, MemoryMaps
from .transformations import projection
from .transformations.merge import is_change, merge_models
from .transformations.projection import DEFAULT_BUDGET, ProjectionSpec


class Datasets:
    def __init__(self, store: ModelStore, sources: Sources | None = None) -> None:
        self.store, self.sources = store, sources

    # -- what is kept ------------------------------------------------------------

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

    # -- Projections ------------------------------------------------------------------

    def draw(self, scene: Scene, state: ProjectionSpec) -> Scene:
        """The projection ``state`` asks for; a change's "changes" preset unfolds what it changed."""
        if state.preset == "changes" and state.focus is None and is_change(scene):
            state.focus = find_review_cut(scene)
        return transformations.apply(scene, projection.fold(state))

    def draw_json(self, scene: Scene, state: ProjectionSpec) -> dict:
        """The projection as JSON."""
        return export_scene(self.draw(scene, state))

    def draw_mermaid(self, scene: Scene, state: ProjectionSpec, fade: bool = True) -> tuple[str, dict[str, str]]:
        """The projection as Mermaid, and each Mermaid node id to the box it is."""
        drawn = self.draw(scene, state)
        return render(drawn, fade=fade), list_ids(drawn)

    def list_changes(self, scene: Scene) -> list[dict]:
        """A change's numbered changes ([] for a commit)."""
        return list_changes(scene) if is_change(scene) else []

    def describe_verdicts(self, scene: Scene) -> list[dict]:
        """A change's kept verdicts with what the viewer says about each."""
        return describe_verdicts(scene, self.load_verdicts(scene)) if is_change(scene) else []

    def read_verdicts(self, scene: Scene, text: str) -> list[dict]:
        """A verdicts.json on the change's review diagram ids, read (ValueError if it is bad)."""
        return read_verdicts(text, find_diagram_ids(scene))

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
        """The ``report.json`` of a model, its counts and the size of its default projection."""
        meta = scene.model.meta
        budget = (meta.get("change") or {}).get("budget") or meta.get("budget") or DEFAULT_BUDGET
        return make_report(scene, self.draw(scene, ProjectionSpec(budget=budget)))

    # -- a review's verdicts ---------------------------------------------------------

    def load_verdicts(self, scene: Scene) -> list[dict]:
        """A change's kept verdicts, in their order."""
        return parse_verdicts(self.store.open_maps("verdicts").load(scene.model.key.format_key()))

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
#
# A target (``nolli build [<target>]``, view, agent brief; nolli/cli.py documents the forms)
# names a model: a dataset already built, a snapshot (one commit, or the files on disk) or a
# change. ``parse_target`` reads its form and ``resolve_target`` finds its commits and key.


@dataclass(frozen=True)
class Target:
    """A target's form, read but not resolved: ``kind`` is ``dataset`` (``path``), ``snapshot``
    (``head``), ``range`` (``base`` and ``head``, either left out; ``symmetric`` for ``...``),
    ``own`` (``head^!``) or ``github`` (``github``)."""
    kind: str
    head: str | None = None
    base: str | None = None
    symmetric: bool = False
    path: Path | None = None
    github: github.Target | None = None


def parse_target(text: str | None, local: Path | None = None) -> Target:
    """The form of ``text`` (None: ``...HEAD``). ``local`` is the repository ``#<n>`` refers
    to. Raises ChangeError for a github.com URL nolli cannot build."""
    if not text:
        return Target("range", head="HEAD", symmetric=True)
    if (Path(text).expanduser() / "key.json").is_file():
        return Target("dataset", path=Path(text).expanduser().resolve())
    gh = github.parse_target(text, local)
    if gh is not None:
        return Target("github", github=gh)
    if "..." in text:
        base, _, head = text.partition("...")
        return Target("range", head=head or None, base=base or None, symmetric=True)
    if ".." in text:
        base, _, head = text.partition("..")
        return Target("range", head=head or None, base=base or None)
    if text.endswith("^!"):
        return Target("own", head=text[:-2])
    return Target("snapshot", head=text)


@dataclass
class Asked:
    """A target, resolved: the model's key, the local repository that has its commits
    (``repo``: yours, or a clone under the data directory), where a build gets them
    (``sources``), what it says about itself (``spec``: base, head, title, description;
    ``about``: also the PR), and for a dataset already built, its directory (``dataset``)."""
    key: ModelKey
    repo: Path | None = None
    spec: ChangeSpec | None = None
    about: dict = field(default_factory=dict)
    sources: Sources | None = None
    dataset: Path | None = None


BUILD_ERRORS = (ChangeError, FetchError, GitError)  # what building from a local repository may raise


def find_repo(repo: Path) -> Repo:
    """A local repository's key: its GitHub remote if it has one (the same commits there are
    the same models), else its path."""
    remote = find_remote(repo)
    found = Repo.from_remote(remote) if remote else None
    return found if found and found.format_remote() else Repo(str(repo.resolve()))


def resolve_target(text: str | None, path: Path, clones: Path, review: bool = False) -> Asked:
    """What the target ``text`` names, for the git repository around ``path`` (a target on
    GitHub is fetched into it, objects only, or into a clone under ``clones`` when ``path`` is
    in none). ``review`` (nolli agent brief): a single revision R is what R adds (``...R``, or
    ``R^!`` if R is on the default branch), and anything that is no change is refused. Raises
    ChangeError."""
    try:
        local: Path | None = find_toplevel(path.resolve())
    except ChangeError:
        local = None
    t = parse_target(text, local)
    if t.kind == "dataset":
        if review:
            raise ChangeError(f"{text} is a dataset built already; name the change to review, or check verdicts: nolli agent check {text}")
        return Asked(read_dataset_key(t.path), dataset=t.path)
    if t.kind == "github":
        asked = _resolve_github(t.github, local, clones)
    else:
        if local is None:
            raise ChangeError(f"{path} is not in a git repository")
        asked = resolve_local(t, local, text, review)
    if review and not asked.spec.is_diff:
        raise ChangeError(f"{text or 'HEAD'} is a snapshot, not a change: a review needs a change "
                          f"(a branch, <base>...<head>, <commit>^! or a pull request)")
    return asked


def resolve_local(t: Target, local: Path, text: str | None = None, review: bool = False) -> Asked:
    """A target of the local repository ``local`` (not a dataset, not on GitHub), resolved;
    ``review`` as for resolve_target. Raises ChangeError."""
    if t.kind == "snapshot" and review and t.head != WORKTREE:
        spec = resolve_adds(local, t.head)
    elif t.kind == "snapshot":
        spec = resolve_snapshot(local, t.head)
    elif t.kind == "own":
        spec = resolve_own(local, t.head)
    else:
        spec = _resolve_range(local, t, text, review)
    r = find_repo(local)
    key: ModelKey = r.make_change(spec.base, spec.head) if spec.is_diff else r.make_commit(spec.head)
    return Asked(key, local, spec, {"title": spec.title, "description": spec.description}, LocalRepository(local))


def _resolve_range(local: Path, t: Target, text: str | None, review: bool = False) -> ChangeSpec:
    try:
        return resolve_range(local, t.base, t.head, t.symmetric)
    except ChangeError as exc:
        if text:
            raise
        if review:
            raise ChangeError(f"no change to review ({exc}); name one: a branch, <base>..<head>, <commit>^! or a pull request") from None
        raise ChangeError(f"nothing to build by default ({exc}); name a target: a branch or commit as a snapshot, "
                          f"...<branch> for what it adds, <base>..<head>, <commit>^!, WORKTREE, or a pull request") from None


def _resolve_github(t: github.Target, local: Path | None, clones: Path) -> Asked:
    """A target on GitHub: asked of GitHub's API, its commits fetched into ``local`` (objects
    only) or into a clone under ``clones``."""
    answer = github.ask_target(t)
    repo = Repo(f"github.com/{t.owner}/{t.name}")
    shas = [s for s in (answer.base, answer.head) if s]
    if local is not None:
        github.fetch_into(local, t.owner, t.name, shas)
        spec, sources, where = github.finish_target(t, answer, local), LocalRepository(local), local
    else:
        cache = github.make_clone_cache(clones)
        with cache.open_commits(repo, *shas) as clone:
            spec, where = github.finish_target(t, answer, clone), clone
        sources = cache
    key: ModelKey = repo.make_change(spec.base, spec.head) if spec.is_diff else repo.make_commit(spec.head)
    return Asked(key, where, spec, {"pr": answer.pr, "title": spec.title, "description": spec.description}, sources)


def read_dataset_key(path: Path) -> ModelKey:
    """The key of the dataset directory ``path``. Raises ChangeError if it has none this nolli reads."""
    store = DirectoryStore(path.parent)
    key = store.find_key(path.name)
    if key is None:
        raise ChangeError(f"{path} is a dataset of another nolli version; build it again")
    return key


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


def write_dataset(asked: Asked, out: Path, about: dict, budget: int | None = None) -> tuple[dict, dict]:
    """``asked`` built here, from scratch, and written to the directory ``out`` (a dataset
    directory, as ``DirectoryStore`` keeps one) with its extras; what it says about itself, and
    the report. ``nolli build -o``."""
    key = asked.key
    about = {k: v for k, v in about.items() if v}
    out.mkdir(parents=True, exist_ok=True)
    if isinstance(key, CommitKey):
        with asked.sources.open_commits(key.repo, key.sha) as clone:
            m, maps = extract_commit(clone, key.sha, key.repo.format_name(), Quiet())
        m.key = key
        if budget:
            m.meta["budget"] = budget
        write_commit(out, key, m, maps)
        keep_commit_scene(DirectoryStore(out.parent), key, {"nolli": version()})  # the directory as nolli serve reads it
        return about, write_extras(Datasets(MemoryMaps()), open_built(m, maps), out)
    sides = {}
    with asked.sources.open_commits(key.repo, key.base, key.head) as clone:
        for side, commit in zip(("base", "head"), key.list_sides(), strict=True):
            m, maps = extract_commit(clone, commit.sha, key.repo.format_name(), Quiet())
            m.key = commit
            sides[side] = (m, maps)
        content = list_content(clone, key.base, key.head, Quiet())
    about.update({"base": key.base, "head": key.head, "remote": key.repo.format_remote(), "nolli": version()})
    scene = merge_built(sides["base"], sides["head"], key, about, content)
    merged = merge_models(key, sides["base"][0], sides["head"][0], describe_change_meta(sides["base"][0], sides["head"][0], about))
    write_change(out, key, about, sides, {"content": content}, merged)
    keep_change_scene(DirectoryStore(out.parent), key, {"nolli": version()})  # the directory as nolli serve reads it
    return about, write_extras(Datasets(MemoryMaps()), scene, out)


def extract_directory(repo: Path, out: Path, budget: int | None = None) -> dict:
    """The model of the files under ``repo``, a directory that is no git repository, written
    to ``out`` with its extras; the report. (A git repository's files on disk are the target
    ``WORKTREE``.)"""
    key = Repo(str(repo.resolve())).make_commit("worktree")
    m, maps = extract(repo, key=key)
    if budget:
        m.meta["budget"] = budget
    out.mkdir(parents=True, exist_ok=True)
    write_commit(out, key, m, maps)
    keep_commit_scene(DirectoryStore(out.parent), key, {"nolli": version()})
    return write_extras(Datasets(MemoryMaps()), open_built(m, maps), out)
