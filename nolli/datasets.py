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
import re
from dataclasses import dataclass, field
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


# -- from what a user names to datasets --------------------------------------------------
#
# A target (``nolli build [<target>]``, view, agent brief; nolli/cli.py documents the forms)
# names a model: a dataset already built, a snapshot (one commit, or the files on disk) or a
# change. ``parse_target`` reads its form and ``resolve_target`` finds its commits and key.


# CALLERS (temp, not to be merged):
#   returned by parse_target; read by cli.py:235 cmd_view (nolli view) and serve.py:294-306 Site.plan (GET /?t=, GET /<owner>/<repo>/…)
#   datasets.py: resolve_target, resolve_local
#   tests: test_targets.py
@dataclass(frozen=True)
class Target:
    """A target's form, read but not resolved: ``kind`` is ``key`` (``key``: a model's key as
    text, already resolved), ``snapshot`` (``head``), ``range`` (``base`` and ``head``, either
    left out; ``symmetric`` for ``...``), ``own`` (``head^!``) or ``github`` (``github``)."""
    kind: str
    head: str | None = None
    base: str | None = None
    symmetric: bool = False
    key: nolli.model.keys.ModelKey | None = None
    github: nolli.logic.sources.github.Target | None = None


# CALLERS (temp, not to be merged):
#   cli.py:231 cmd_view — nolli view
#   serve.py:294 Site.plan — GET /?t=, GET /<owner>/<repo>/…
#   datasets.py: resolve_target
#   tests: test_targets.py
def parse_target(text: str | None, local: Path | None = None) -> Target:
    """The form of ``text`` (None: ``...HEAD``). ``local`` is the repository ``#<n>`` refers
    to. Raises ChangeError for a github.com URL nolli cannot build."""
    if not text:
        return Target("range", head="HEAD", symmetric=True)
    key = _parse_key(text)
    if key is not None:
        return Target("key", key=key)
    gh = nolli.logic.sources.github.parse_target(text, local)
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


def _parse_key(text: str) -> nolli.model.keys.ModelKey | None:
    """The key ``text`` is (``<repo>@<sha>`` or ``<repo>@<base>..<head>``, full shas), None for
    anything else: no git revision has a repository before an ``@`` and 40 hex digits after."""
    key = nolli.model.keys.ModelKey.parse(text)
    shas = (key.base, key.head) if isinstance(key, nolli.model.keys.ChangeKey) else (getattr(key, "sha", ""),)
    return key if all(re.fullmatch(r"[0-9a-f]{40}", s) for s in shas) else None


# CALLERS (temp, not to be merged):
#   returned by resolve_target / resolve_local; read by cli.py resolve/build/build_into/Built (nolli build [-o], nolli view --rebuild, nolli agent brief, nolli extract, nolli diff)
#   and serve.py:306-307 Site.plan — GET /?t=<local revision> (view only)
#   datasets.py: write_dataset
@dataclass
class Asked:
    """A target, resolved: the model's key, the local repository that has its commits
    (``repo``: yours, or a clone under the data directory), where a build gets them
    (``sources``), what it says about itself (``spec``: base, head, title, description;
    ``about``: also the PR), and for a dataset already built, its directory (``dataset``)."""
    key: nolli.model.keys.ModelKey
    repo: Path | None = None
    spec: nolli.logic.sources.git.ChangeSpec | None = None
    about: dict = field(default_factory=dict)
    sources: nolli.logic.sources.Sources | None = None
    dataset: Path | None = None


# CALLERS (temp, not to be merged):
#   cli.py:122 resolve — nolli build [-o], nolli view --rebuild, nolli agent brief, nolli extract, nolli diff
#   cli.py:154 build() — nolli build, nolli view --rebuild, nolli agent brief
#   cli.py:206 build_into — nolli build -o, nolli extract (git repo), nolli diff
BUILD_ERRORS = (nolli.logic.sources.git.ChangeError, nolli.logic.sources.FetchError, nolli.logic.sources.git.GitError)  # what building from a local repository may raise


# CALLERS (temp, not to be merged):
#   datasets.py only: resolve_local
#   tests: test_diff.py
def find_repo(repo: Path) -> nolli.model.keys.Repo:
    """A local repository's key: its GitHub remote if it has one (the same commits there are
    the same models), else its path."""
    remote = nolli.logic.sources.git.find_remote(repo)
    found = nolli.model.keys.Repo.from_remote(remote) if remote else None
    return found if found and found.format_remote() else nolli.model.keys.Repo(str(repo.resolve()))


# CALLERS (temp, not to be merged):
#   cli.py:121 resolve — nolli build [-o], nolli view --rebuild, nolli agent brief, nolli extract, nolli diff
#   tests: test_targets.py
def resolve_target(text: str | None, path: Path, clones: Path, review: bool = False) -> Asked:
    """What the target ``text`` names, for the git repository around ``path`` (a target on
    GitHub is fetched into it, objects only, or into a clone under ``clones`` when ``path`` is
    in none). ``review`` (nolli agent brief): a single revision R is what R adds (``...R``, or
    ``R^!`` if R is on the default branch), and anything that is no change is refused. Raises
    ChangeError."""
    try:
        local: Path | None = nolli.logic.sources.git.find_toplevel(path.resolve())
    except nolli.logic.sources.git.ChangeError:
        local = None
    t = parse_target(text, local)
    if t.kind == "key":
        asked = _resolve_key(t.key, local, clones)
    elif t.kind == "github":
        asked = _resolve_github(t.github, local, clones)
    else:
        if local is None:
            raise nolli.logic.sources.git.ChangeError(f"{path} is not in a git repository")
        asked = resolve_local(t, local, text, review)
    if review and not asked.spec.is_diff:
        raise nolli.logic.sources.git.ChangeError(f"{text or 'HEAD'} is a snapshot, not a change: a review needs a change "
                          f"(a branch, <base>...<head>, <commit>^! or a pull request)")
    return asked


# CALLERS (temp, not to be merged):
#   serve.py:306 Site.plan — GET /?t=<local revision> (view only)
#   datasets.py: resolve_target
def resolve_local(t: Target, local: Path, text: str | None = None, review: bool = False) -> Asked:
    """A target of the local repository ``local`` (not a dataset, not on GitHub), resolved;
    ``review`` as for resolve_target. Raises ChangeError."""
    if t.kind == "snapshot" and review and t.head != nolli.logic.sources.git.WORKTREE:
        spec = nolli.logic.sources.git.resolve_adds(local, t.head)
    elif t.kind == "snapshot":
        spec = nolli.logic.sources.git.resolve_snapshot(local, t.head)
    elif t.kind == "own":
        spec = nolli.logic.sources.git.resolve_own(local, t.head)
    else:
        spec = _resolve_range(local, t, text, review)
    r = find_repo(local)
    key: nolli.model.keys.ModelKey = r.make_change(spec.base, spec.head) if spec.is_diff else r.make_commit(spec.head)
    return Asked(key, local, spec, {"title": spec.title, "description": spec.description}, nolli.logic.sources.LocalRepository(local))


def _resolve_key(key: nolli.model.keys.ModelKey, local: Path | None, clones: Path) -> Asked:
    """A key, resolved already: where its commits are, should it need building (``local`` if
    it is that repository's, else a clone of its GitHub remote, else the repository at its path)."""
    if local is not None and find_repo(local) == key.repo:
        where, sources = local, nolli.logic.sources.LocalRepository(local)
    elif key.repo.format_remote():
        where, sources = None, nolli.logic.sources.github.make_clone_cache(clones)
    else:
        where = Path(key.repo.url)
        sources = nolli.logic.sources.LocalRepository(where)
    change = isinstance(key, nolli.model.keys.ChangeKey)
    spec = nolli.logic.sources.git.ChangeSpec(head=key.head if change else key.sha, base=key.base if change else None)
    return Asked(key, where, spec, {}, sources)


def _resolve_range(local: Path, t: Target, text: str | None, review: bool = False) -> nolli.logic.sources.git.ChangeSpec:
    try:
        return nolli.logic.sources.git.resolve_range(local, t.base, t.head, t.symmetric)
    except nolli.logic.sources.git.ChangeError as exc:
        if text:
            raise
        if review:
            raise nolli.logic.sources.git.ChangeError(f"no change to review ({exc}); name one: a branch, <base>..<head>, <commit>^! or a pull request") from None
        raise nolli.logic.sources.git.ChangeError(f"nothing to build by default ({exc}); name a target: a branch or commit as a snapshot, "
                          f"...<branch> for what it adds, <base>..<head>, <commit>^!, WORKTREE, or a pull request") from None


def _resolve_github(t: nolli.logic.sources.github.Target, local: Path | None, clones: Path) -> Asked:
    """A target on GitHub: asked of GitHub's API, its commits fetched into ``local`` (objects
    only) or into a clone under ``clones``."""
    answer = nolli.logic.sources.github.ask_target(t)
    repo = nolli.model.keys.Repo(f"github.com/{t.owner}/{t.name}")
    shas = [s for s in (answer.base, answer.head) if s]
    if local is not None:
        nolli.logic.sources.github.fetch_into(local, t.owner, t.name, shas)
        spec, sources, where = nolli.logic.sources.github.finish_target(t, answer, local), nolli.logic.sources.LocalRepository(local), local
    else:
        cache = nolli.logic.sources.github.make_clone_cache(clones)
        with cache.open_commits(repo, *shas) as clone:
            spec, where = nolli.logic.sources.github.finish_target(t, answer, clone), clone
        sources = cache
    key: nolli.model.keys.ModelKey = repo.make_change(spec.base, spec.head) if spec.is_diff else repo.make_commit(spec.head)
    return Asked(key, where, spec, {"pr": answer.pr, "title": spec.title, "description": spec.description}, sources)


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
