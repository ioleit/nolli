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


def _fetch_whole_model(store: ModelStore, sources: Sources, commit: CommitKey, *, run: Run = _run_here,
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


def _fetch_change(store: ModelStore, sources: Sources, change: ChangeKey, *, about: dict | None = None,
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


# -- targets: what a user names, and building it -------------------------------------------
#
# A target (``nolli build [<target>]``, view, agent brief, the server's ``?t=``; nolli/cli.py
# documents the forms) names a model. Some forms move (a branch, WORKTREE, a pull request):
# ``Target.pin`` says what one names now, cheaply, as a stable target. ``build`` builds any
# target (pinning it if it is not stable); what it resolves to is found on the way.


@dataclass(frozen=True)
class Target:
    """What a user names, read but not looked up. ``kind``:

    - ``key``: a model's key (``key``), resolved already;
    - ``pinned``: the change of ``repo`` from where ``head`` left ``base`` (full shas; as text
      ``<repo>@<base>...<head>``): stable, but where they diverged is found in their commits;
    - ``github``: something on GitHub (``github``), which moves;
    - ``revs``: git's revisions (``revs``) of the local repository ``local``, which move.

    ``text`` is what the user wrote; ``about`` what a pinned target says about itself
    (``title``, ``description``, ``pr``), kept with the model. Neither is part of what it is."""
    kind: str
    key: ModelKey | None = None
    repo: Repo | None = None
    base: str | None = None
    head: str | None = None
    github: github.Target | None = None
    revs: git.Revs | None = None
    local: Path | None = None
    text: str | None = field(default=None, compare=False)
    about: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_string(cls, text: str | None, local: Path | None = None) -> Target:
        """The form of ``text`` (None: ``...HEAD``). ``local`` is the repository git's revisions
        and ``#<n>`` refer to. Raises ChangeError for a github.com URL nolli cannot build."""
        if not text:
            return cls("revs", revs=git.Revs("range", head="HEAD", symmetric=True), local=local)
        stable = _parse_stable(text)
        if stable is not None:
            return stable
        gh = github.parse_target(text, local)
        if gh is not None:
            return cls("github", github=gh, text=text)
        return cls("revs", revs=git.parse_revs(text), local=local, text=text)

    @property
    def is_stable(self) -> bool:
        """It names the same model whenever it is asked: a key, or pinned."""
        return self.kind in ("key", "pinned")

    def format(self) -> str:
        """A stable target as text, which ``from_string`` reads back."""
        if self.kind == "key":
            return self.key.format_key()
        if self.kind == "pinned":
            return f"{self.repo.url}@{self.base}...{self.head}"
        raise ValueError(f"a {self.kind} target is not stable: pin it")

    def pin(self, get: Callable[[str], dict] | None = None, review: bool = False) -> Target:
        """What this target names now, as a stable target with what it says about itself: git's
        revisions looked up in the local repository, a target on GitHub asked of GitHub's API
        (``get``, as ``github.ask_target`` takes it); no commits are fetched. ``review`` (nolli
        agent brief): a single revision R is what R adds (``...R``, or ``R^!`` if R is on the
        default branch), and anything that is no change is refused. Raises ChangeError."""
        if self.kind == "revs":
            pinned = self._pin_revs(review)
        elif self.kind == "github":
            pinned = self._pin_github(get)
        else:
            pinned = self
        if review and not (pinned.kind == "pinned" or isinstance(pinned.key, ChangeKey)):
            raise git.ChangeError(f"{self.text or 'HEAD'} is a snapshot, not a change: a review needs a change "
                                  f"(a branch, <base>...<head>, <commit>^! or a pull request)")
        return pinned

    def _pin_revs(self, review: bool) -> Target:
        if self.local is None:
            raise git.ChangeError(f"{self.text or 'HEAD'}: not in a git repository")
        try:
            spec = git.resolve_revs(self.local, self.revs, adds=review)
        except git.ChangeError as exc:
            if self.text:
                raise
            if review:
                raise git.ChangeError(f"no change to review ({exc}); name one: a branch, <base>..<head>, <commit>^! or a pull request") from None
            raise git.ChangeError(f"nothing to build by default ({exc}); name a target: a branch or commit as a snapshot, "
                                  f"...<branch> for what it adds, <base>..<head>, <commit>^!, WORKTREE, or a pull request") from None
        repo = LocalRepository(self.local).find_repo()
        key = repo.make_change(spec.base, spec.head) if spec.is_diff else repo.make_commit(spec.head)
        return Target("key", key=key, text=self.text, about={"title": spec.title, "description": spec.description})

    def _pin_github(self, get: Callable[[str], dict] | None) -> Target:
        t = self.github
        a = github.ask_target(t, get)
        repo = Repo(f"github.com/{t.owner}/{t.name}")
        about = {"pr": a.pr, "title": a.title, "description": a.description}
        if a.base is None:
            return Target("key", key=repo.make_commit(a.head), text=self.text, about=about)
        if a.base == a.head:
            raise git.ChangeError(f"{t.owner}/{t.name}: no change ({a.head[:7]} is {a.base[:7]})")
        if a.merge:
            return Target("pinned", repo=repo, base=a.base, head=a.head, text=self.text, about=about)
        return Target("key", key=repo.make_change(a.base, a.head), text=self.text, about=about)


def _parse_stable(text: str) -> Target | None:
    """The stable target ``text`` is: a key (``<repo>@<sha>``, ``<repo>@<base>..<head>``) or
    pinned (``<repo>@<base>...<head>``), full shas; None for anything else: no git revision has
    a repository before an ``@`` and 40 hex digits after."""
    repo, at, rest = text.rpartition("@")
    if not (at and repo and rest):
        return None
    shas = rest.split("...") if "..." in rest else rest.split("..")
    if not 1 <= len(shas) <= 2 or not all(re.fullmatch(r"[0-9a-f]{40}", s) for s in shas):
        return None
    if "..." in rest:
        return Target("pinned", repo=Repo(repo), base=shas[0], head=shas[1], text=text)
    return Target("key", key=ModelKey.parse(text), text=text)


def resolve(target: Target, sources: Sources, progress: Progress | None = None) -> ModelKey:
    """The key ``target`` names (pinned first if it is not stable). For a change from where its
    head left its base, that is found in its commits, which ``sources`` has or fetches."""
    t = target if target.is_stable else target.pin()
    if t.kind == "key":
        return t.key
    with (progress or Quiet()).step("fetching the commits"), sources.open_commits(t.repo, t.base, t.head) as clone:
        base = git.merge_base(clone, t.base, t.head)
    if base == t.head:
        raise git.ChangeError(f"{t.repo.format_name()}: no change ({t.head[:7]} is on {t.base[:7]})")
    return t.repo.make_change(base, t.head)


def build(store: ModelStore, target: Target, sources: Sources, *, about: dict | None = None, run: Run = _run_here,
          progress: Progress | None = None, rebuild: bool = False) -> tuple[ModelKey, bool]:
    """Build what ``target`` names (pinned first if it is not stable) into ``store``, its commits
    from ``sources``, unless a fresh one is kept there: its key, and whether it was built.
    ``about``: more of what a change says about itself (``budget``; what the user said of it),
    over what the target says. ``run``: where the steps that take time run."""
    target = target if target.is_stable else target.pin()
    key = resolve(target, sources, progress)
    kept = None if rebuild else store.load_change(key) if isinstance(key, ChangeKey) else store.load_model(key)
    if is_fresh(kept):
        return key, False
    if isinstance(key, ChangeKey):
        _fetch_change(store, sources, key, about={**target.about, **(about or {})}, run=run, progress=progress, rebuild=rebuild)
    else:
        _fetch_whole_model(store, sources, key, run=run, progress=progress, rebuild=rebuild)
    return key, True
