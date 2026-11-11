"""Builds models from a repository's files and keeps them in a store.

A model or change is taken from the store when it has one built by this version, else it is
extracted from the repository's commits and kept there.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nolli import scenes
from nolli.logic.extractors import facts
from nolli.logic.sources import LocalRepository, Sources, git, github
from nolli.logic.transformations import merge
from nolli.model.keys import ChangeKey, CommitKey, ModelKey, Repo, StringKey
from nolli.model.model import Key, Model
from nolli.model.schema import EntityId
from nolli.progress import Progress, Quiet
from nolli.store import ModelStore
from nolli.store.maps import Maps, MapStore

# run(fn, *args, progress=…) calls fn(*args, progress=…), here or elsewhere (in a process of
# its own). The steps that take time (extract_commit, list_content) take paths and return
# data, so that a caller can run them elsewhere.
Run = Callable[..., Any]


def _run_here(fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
    return fn(*args, progress=progress)


def extract(repo: Path, config: dict | None = None, progress: Progress | None = None, key: ModelKey = StringKey()) -> tuple[Model, Maps]:
    """The model of the files under ``repo`` as they are (a working tree, or a checkout), and
    what each extractor said about its nodes and edges."""
    found = facts.extract_facts(repo, git.list_tracked_files(repo), config, progress)
    head = git.find_head(repo)
    found.meta.update({"repo": str(repo), "commit": head[:7] if head else None, "nolli": version()})
    return found.split(key)


def extract_commit(repo: Path, sha: str, name: str, progress: Progress) -> tuple[Model, Maps]:
    """The model of commit ``sha`` of the git repository ``repo`` (its files, not a working
    tree), its root named ``name``."""
    with tempfile.TemporaryDirectory(prefix="nolli-commit-") as tmp:
        with progress.step("checking out the commit"):
            git.export_commit(repo, sha, Path(tmp))
        m, maps = extract(Path(tmp), progress=progress)
    for values in maps.values():  # the root is the temporary directory
        if EntityId.REPO_ROOT in values and "name" in values[EntityId.REPO_ROOT]:
            values[EntityId.REPO_ROOT]["name"] = name
    m.meta.update({"repo": name, "commit": sha[:7]})
    return m, maps


def list_content(repo: Path, base: str, head: str, progress: Progress) -> dict[Key, dict]:
    """The ``content`` map of a change: what git says changed in each file, by the file's
    address (its head path; base path for a deletion)."""
    with progress.step("comparing the commits"):
        return {EntityId.from_path(path): c.to_dict() for path, c in git.list_file_changes(repo, base, head).items()}


def version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("nolli-review")
    except PackageNotFoundError:
        return "unknown"


def is_fresh(m: Model | dict | None) -> bool:
    """Built by this version of nolli (an older model is built again when it is asked for)."""
    meta = m.meta if isinstance(m, Model) else (m or {})
    return meta.get("nolli") == version()


# -- from the store, or built ---------------------------------------------------------


def _load_maps(store: MapStore, m: Model) -> Maps:
    return {n: dict(store.open_maps(n).load(m.key.format_key())) for n in store.list_map_names(m.key.format_key())}


def _fetch_commit_model(store: ModelStore, clone: Path, commit: CommitKey, *, run: Run = _run_here,
                       progress: Progress | None = None, rebuild: bool = False) -> tuple[Model, Maps]:
    """The model of one commit and the maps kept for it: from the store, or extracted from
    ``clone`` (which has the commit)."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m):
        return m, _load_maps(store, m)
    m, maps = run(extract_commit, clone, commit.sha, commit.repo.format_name(), progress=progress)
    m.key = commit
    return m, maps


def fetch_whole_model(store: ModelStore, sources: Sources, commit: CommitKey, *, run: Run = _run_here,
                      progress: Progress | None = None, rebuild: bool = False) -> Model:
    """A repository at one commit, as the viewer shows a repository as a whole: kept in the store."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m):
        return m
    with sources.open_commits(commit.repo, commit.sha) as clone:
        m, maps = _fetch_commit_model(store, clone, commit, run=run, progress=progress, rebuild=True)
    with progress.step("keeping the model"):
        store.save_model(commit, m, maps)
        scenes.keep_commit_scene(store, commit, {"nolli": version()})
    return m


def fetch_change(store: ModelStore, sources: Sources, change: ChangeKey, *, about: dict | None = None,
                 run: Run = _run_here, progress: Progress | None = None, rebuild: bool = False) -> dict:
    """A change: from the store, or built from its two commits (their models, from the store
    if it has them) and git's hunks between them, and kept there. ``about``: what the change
    says about itself (``pr``, ``title``, ``description``, ``budget``), kept with it."""
    progress = progress or Quiet()
    kept = None if rebuild else store.load_change(change)
    if kept is not None and is_fresh(kept):
        return kept
    with sources.open_commits(change.repo, change.base, change.head) as clone:
        sides = {side: _fetch_commit_model(store, clone, c, run=run, progress=progress.under(side), rebuild=rebuild)
                 for c, side in zip(change.list_sides(), ("base", "head"), strict=True)}
        content = run(list_content, clone, change.base, change.head, progress=progress)
    record = {k: v for k, v in (about or {}).items() if v}
    record.update({"base": change.base, "head": change.head, "remote": change.repo.format_remote(), "nolli": version()})
    with progress.step("keeping the change"):
        meta = scenes.describe_change_meta(sides["base"][0], sides["head"][0], record)
        store.save_change(change, record, sides, {"content": content}, merge.merge_models(change, sides["base"][0], sides["head"][0], meta))
        scenes.keep_change_scene(store, change, {"nolli": version()})
    return record


# -- targets: what a user names, resolved to a model's key --------------------------------
#
# A target (``nolli build [<target>]``, view, agent brief, the server's ``?t=``; nolli/cli.py
# documents the forms) names a model: its key, resolved already; something on GitHub; or
# git's revisions of the local repository (a snapshot, a range, a commit's own change).


@dataclass(frozen=True)
class Target:
    """A target's form, read but not looked up: ``kind`` is ``key`` (``key``), ``github``
    (``github``) or ``revs`` (``revs``)."""
    kind: str
    key: ModelKey | None = None
    github: github.Target | None = None
    revs: git.Revs | None = None

    @classmethod
    def from_string(cls, text: str | None, local: Path | None = None) -> Target:
        """The form of ``text`` (None: ``...HEAD``). ``local`` is the repository ``#<n>`` refers
        to. Raises ChangeError for a github.com URL nolli cannot build."""
        if not text:
            return cls("revs", revs=git.Revs("range", head="HEAD", symmetric=True))
        key = _parse_key(text)
        if key is not None:
            return cls("key", key=key)
        gh = github.parse_target(text, local)
        if gh is not None:
            return cls("github", github=gh)
        return cls("revs", revs=git.parse_revs(text))


def _parse_key(text: str) -> ModelKey | None:
    """The key ``text`` is (``<repo>@<sha>`` or ``<repo>@<base>..<head>``, full shas), None for
    anything else: no git revision has a repository before an ``@`` and 40 hex digits after."""
    key = ModelKey.parse(text)
    shas = (key.base, key.head) if isinstance(key, ChangeKey) else (getattr(key, "sha", ""),)
    return key if all(re.fullmatch(r"[0-9a-f]{40}", s) for s in shas) else None


@dataclass
class ResolvedTarget:
    """A target, resolved: the model's key, the local repository that has its commits
    (``repo``: yours, or a clone under the data directory), where a build gets them
    (``sources``), what it says about itself (``spec``: base, head, title, description;
    ``about``: also the PR), and for a dataset already built, its directory (``dataset``)."""
    key: ModelKey
    repo: Path | None = None
    spec: git.ChangeSpec | None = None
    about: dict = field(default_factory=dict)
    sources: Sources | None = None
    dataset: Path | None = None


def resolve_target(text: str | None, path: Path, clones: Path, review: bool = False) -> ResolvedTarget:
    """What the target ``text`` names, for the git repository around ``path`` (a target on
    GitHub is fetched into it, objects only, or into a clone under ``clones`` when ``path`` is
    in none). ``review`` (nolli agent brief): a single revision R is what R adds (``...R``, or
    ``R^!`` if R is on the default branch), and anything that is no change is refused. Raises
    ChangeError."""
    try:
        local: Path | None = git.find_toplevel(path.resolve())
    except git.ChangeError:
        local = None
    t = Target.from_string(text, local)
    if t.kind == "key":
        resolved = _resolve_key(t.key, local, clones)
    elif t.kind == "github":
        answer, spec, where, sources = github.resolve(t.github, local, clones)
        resolved = ResolvedTarget(_make_key(Repo(f"github.com/{t.github.owner}/{t.github.name}"), spec), where, spec,
                                  {"pr": answer.pr, "title": spec.title, "description": spec.description}, sources)
    else:
        if local is None:
            raise git.ChangeError(f"{path} is not in a git repository")
        resolved = resolve_local(t, local, text, review)
    if review and not resolved.spec.is_diff:
        raise git.ChangeError(f"{text or 'HEAD'} is a snapshot, not a change: a review needs a change "
                              f"(a branch, <base>...<head>, <commit>^! or a pull request)")
    return resolved


def resolve_local(t: Target, local: Path, text: str | None = None, review: bool = False) -> ResolvedTarget:
    """Git's revisions of the local repository ``local``, resolved; ``review`` as for
    resolve_target. Raises ChangeError, saying what to name if ``text`` named nothing."""
    try:
        spec = git.resolve_revs(local, t.revs, adds=review)
    except git.ChangeError as exc:
        if text:
            raise
        if review:
            raise git.ChangeError(f"no change to review ({exc}); name one: a branch, <base>..<head>, <commit>^! or a pull request") from None
        raise git.ChangeError(f"nothing to build by default ({exc}); name a target: a branch or commit as a snapshot, "
                              f"...<branch> for what it adds, <base>..<head>, <commit>^!, WORKTREE, or a pull request") from None
    repo = LocalRepository(local)
    return ResolvedTarget(_make_key(repo.find_repo(), spec), local, spec, {"title": spec.title, "description": spec.description}, repo)


def _resolve_key(key: ModelKey, local: Path | None, clones: Path) -> ResolvedTarget:
    """A key, resolved already: where its commits are, should it need building (``local`` if
    it is that repository's, else a clone of its GitHub remote, else the repository at its path)."""
    sources: Sources
    if local is not None and LocalRepository(local).find_repo() == key.repo:
        where, sources = local, LocalRepository(local)
    elif key.repo.format_remote():
        where, sources = None, github.make_clone_cache(clones)
    else:
        where = Path(key.repo.url)
        sources = LocalRepository(where)
    change = isinstance(key, ChangeKey)
    spec = git.ChangeSpec(head=key.head if change else key.sha, base=key.base if change else None)
    return ResolvedTarget(key, where, spec, {}, sources)


def _make_key(repo: Repo, spec: git.ChangeSpec) -> ModelKey:
    return repo.make_change(spec.base, spec.head) if spec.is_diff else repo.make_commit(spec.head)
