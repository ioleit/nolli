"""High-level operations that tie nolli's store and its logic together, shared by the command
line (``nolli/cli.py``) and the server (``nolli/server/``).

A target (what a user names: a key, a branch, a pull request, …) is built into a store
(``build_target``): taken from the store when it holds one built by this version, else extracted
from the repository's commits and kept there. A stored model is opened as a scene
(``open_scene``), with what extraction said and nolli's overlays; the values of the overlays
that cost the most are computed when the model is built and kept with it. Comments on a model
(``add_comments``) are kept on the model it is a view of and shown on every scene of that one.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nolli.logic import comments as comments_lib
from nolli.logic import transformations
from nolli.logic.extractors import facts
from nolli.logic.sources import LocalRepository, Sources, git, github
from nolli.logic.transformations import merge, projection
from nolli.logic.transformations.merge import presets
from nolli.logic.transformations.merge.change import Change
from nolli.model import vocabulary
from nolli.model import keys
from nolli.model.keys import ChangeKey, CommitKey, ModelKey, ProjectionKey, Repo, SelectionKey, StringKey
from nolli.model.model import Key, Model
from nolli.model.scene import Overlay, Scene
from nolli.model.schema import EntityId
from nolli.progress import Progress, Quiet
from nolli.store import ModelStore
from nolli.store import precomputed
from nolli.store.maps import Maps


# -- targets, and building them ---------------------------------------------------------
#
# A target (``nolli build [<target>]``, view, agent brief, the server's ``?t=``; nolli/cli.py
# documents the forms) names a model. Some forms move (a branch, WORKTREE, a pull request):
# ``Target.pin`` says what one names now, cheaply, as a stable target. ``build_target`` builds any
# target (pinning it if it is not stable); what it resolves to is found on the way.


# run(fn, *args, progress=…) calls fn(*args, progress=…), here or elsewhere (in a process of
# its own). The steps that take time (_extract_commit, _list_content) take paths and return
# data, so that a caller can run them elsewhere.
Run = Callable[..., Any]


def _run_here(fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
    return fn(*args, progress=progress)


def _version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("nolli-review")
    except PackageNotFoundError:
        return "unknown"


def is_fresh(m: Model | dict | None) -> bool:
    """Tell whether ``m`` (a model, or a change's record) was built by this version of nolli; an
    older one is built again when it is asked for."""
    meta = m.meta if isinstance(m, Model) else (m or {})
    return meta.get("nolli") == _version()


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
        """Parse the form of the target ``text``; no text means ``...HEAD``. ``local`` is the repository
        that git's revisions and ``#<n>`` refer to. Raise ChangeError for a github.com URL nolli
        cannot build."""
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
        """Format a stable target as text, which ``from_string`` reads back. Raise ValueError for a
        target that is not stable."""
        if self.kind == "key":
            return self.key.format_key()
        if self.kind == "pinned":
            return f"{self.repo.url}@{self.base}...{self.head}"
        raise ValueError(f"a {self.kind} target is not stable: pin it")

    @property
    def is_change(self) -> bool:
        """It names a change, not a snapshot: pinned, or a change's key. Ask it of a stable target."""
        return self.kind == "pinned" or isinstance(self.key, ChangeKey)

    def pin(self, get: Callable[[str], dict] | None = None, adds: bool = False) -> Target:
        """Pin this target: return a stable target for what it names now, with what it says about
        itself in ``about``. Git's revisions are looked up in the local repository, and a target on
        GitHub is asked of GitHub's API (``get``, as ``github.ask_target`` takes it); no commits are
        fetched. With ``adds``, a single revision R means what R adds (``...R``, or ``R^!`` if R is
        on the default branch) rather than a snapshot of R. Raise ChangeError."""
        if self.kind == "revs":
            return self._pin_revs(adds)
        if self.kind == "github":
            return self._pin_github(get)
        return self

    def _pin_revs(self, adds: bool) -> Target:
        if self.local is None:
            raise git.ChangeError(f"{self.text or 'HEAD'}: not in a git repository")
        try:
            spec = git.resolve_revs(self.local, self.revs, adds=adds)
        except git.ChangeError as exc:
            if self.text:
                raise
            if adds:
                raise git.ChangeError(f"no change by default ({exc}); name one: a branch, <base>..<head>, <commit>^! or a pull request") from None
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
    """Parse ``text`` as a stable target: a key (``<repo>@<sha>``, ``<repo>@<base>..<head>``) or
    pinned (``<repo>@<base>...<head>``), with full shas. Return None for anything else; no git
    revision has a repository before an ``@`` and 40 hex digits after it."""
    repo, at, rest = text.rpartition("@")
    if not (at and repo and rest):
        return None
    shas = rest.split("...") if "..." in rest else rest.split("..")
    if not 1 <= len(shas) <= 2 or not all(re.fullmatch(r"[0-9a-f]{40}", s) for s in shas):
        return None
    if "..." in rest:
        return Target("pinned", repo=Repo(repo), base=shas[0], head=shas[1], text=text)
    return Target("key", key=ModelKey.parse(text), text=text)


def resolve_target(target: Target, sources: Sources, progress: Progress | None = None) -> ModelKey:
    """Return the key that ``target`` names, pinning it first if it is not stable. For a change
    from where its head left its base, find where they diverged in its commits, which
    ``sources`` has or fetches. Raise ChangeError if they did not diverge."""
    t = target if target.is_stable else target.pin()
    if t.kind == "key":
        return t.key
    with (progress or Quiet()).step("fetching the commits"), sources.open_commits(t.repo, t.base, t.head) as clone:
        base = git.merge_base(clone, t.base, t.head)
    if base == t.head:
        raise git.ChangeError(f"{t.repo.format_name()}: no change ({t.head[:7]} is on {t.base[:7]})")
    return t.repo.make_change(base, t.head)


def build_target(store: ModelStore, target: Target, sources: Sources, *, about: dict | None = None, run: Run = _run_here,
          progress: Progress | None = None, rebuild: bool = False) -> ModelKey:
    """Build what ``target`` names into ``store``, unless a fresh one is kept there, and return its
    key. A target that is not stable is pinned first; its commits come from ``sources``. ``about``
    adds to what a change says about itself (``budget``, what the user said of it), over what the
    target says; ``run`` says where the steps that take time run."""
    target = target if target.is_stable else target.pin()
    key = resolve_target(target, sources, progress)
    kept = None if rebuild else store.load_change(key) if isinstance(key, ChangeKey) else store.load_model(key)
    if is_fresh(kept):
        return key
    if isinstance(key, ChangeKey):
        _fetch_change(store, sources, key, about={**target.about, **(about or {})}, run=run, progress=progress, rebuild=rebuild)
    else:
        _fetch_whole_model(store, sources, key, run=run, progress=progress, rebuild=rebuild)
    return key


def build_files(store: ModelStore, path: Path, key: ModelKey, progress: Progress | None = None) -> ModelKey:
    """Build the files under ``path`` as they are (a working tree, a checkout, a directory that is
    no git repository) into ``store`` under ``key``, and return the key. They are built every
    time: they are no commit, so nothing says a kept model of them is fresh."""
    model, maps = _extract_files(path, progress=progress, key=key)
    store.save_model(key, model, maps)  # type: ignore[arg-type]  # a store keeps a model under any key it can hold
    return key


# -- opening a stored model as a scene ---------------------------------------------------


def _make_derived_overlays() -> list[Overlay]:
    """Make the overlays nolli derives from any model's facts and keeps with it at build time
    (``precompute_scene``): what its nodes are called (``names``)."""
    from nolli.logic.formats.descriptions.names import Names

    return [Names()]


def _make_kept_overlays(store: ModelStore, key: ModelKey) -> list[Overlay]:
    """Make the overlays a stored model keeps the values of (``precompute_scene``) and answers from
    when it is opened: what nolli derives from any model's facts, and for a change what it says
    about itself."""
    return [*_make_derived_overlays(), *(_make_change_overlays(store) if isinstance(key, ChangeKey) else [])]


def _make_change_overlays(store: ModelStore) -> list[Overlay]:
    """Make the overlays that compute what a change says about itself from its sides: what changed
    (``change``). Its values are kept with the change at diff time (``precompute_scene``) and
    read back from there (``_open_change_scene``)."""
    return [Change(store.open_maps("content"))]


def _describe_scene_meta(base: Model, head: Model, about: dict, content: Mapping[Key, dict]) -> dict:
    return {**_describe_change_meta(base, head, about), "change": merge.describe_change(about, content)}


def _make_overlays(store: ModelStore) -> list[Overlay]:
    """Make the overlays nolli shows on any scene, on top of what extraction said: among them the
    comments kept in ``store``."""
    from nolli.logic.formats.descriptions.hover import Hover
    from nolli.logic.formats.descriptions.label import Label

    derived = [precomputed.caching_overlay(store, o) for o in _make_derived_overlays()]
    return [*derived, Change(store.open_maps("content")), comments_lib.Comments(store.load_comments), Label(), Hover()]


def _open_scene(model: Model, store: ModelStore) -> Scene:
    """Open a model as a scene with overlays for what extraction said, by shape and read by
    address, followed by nolli's own overlays. A shape never extracted for this model says
    nothing; a model made from this one answers for a namesake with the same values."""
    said = [precomputed.precomputed_overlay(store, n) for n in vocabulary.SHAPES]
    return Scene(model, [*said, *_make_overlays(store)])


def _open_commit_scene(store: ModelStore, commit: CommitKey) -> Scene | None:
    """Open a commit's stored model as a scene; return None if the store does not have it."""
    m = store.load_model(commit)
    return None if m is None else _open_scene(m, store)


def _open_change_scene(store: ModelStore, change: ChangeKey) -> Scene | None:
    """Open the scene of a change between two stored commits, or return None if the change or either
    commit is not stored. The scene uses the merged model kept with the change (or merges one here
    if none was kept), puts what the change says about itself in ``meta["change"]``, and answers
    its own overlays from what was kept at diff time, computing those that were not kept."""
    about = store.load_change(change)
    if about is None:
        return None
    base, head = (_open_commit_scene(store, c) for c in change.list_sides())
    if base is None or head is None:
        return None
    meta = _describe_scene_meta(base.model, head.model, about, store.open_maps("content").load(change.format_key()))
    scene = transformations.apply({"base": base, "head": head}, merge.union(change, meta, store.load_model(change)))
    return scene.with_overlays(*[precomputed.caching_overlay(store, o) for o in _make_kept_overlays(store, change)])


def open_scene(store: ModelStore, key: ModelKey, overlays: Sequence[Overlay] = ()) -> Scene | None:
    """Open the scene of a stored model with what extraction said and nolli's own overlays, and
    ``overlays`` on top (one of the same name replaces nolli's). The model is a change merged from
    its commits, a single commit or a model kept under a name, or a projection of any of these,
    drawn again from its key. Return None if the store does not have it, or for a key nolli cannot
    draw again (a selection's); raise NotAllowed as the store does."""
    if isinstance(key, ProjectionKey):
        source = open_scene(store, key.source)
        if source is None:
            return None
        scene = transformations.apply(source, projection.fold(projection.ProjectionSpec.parse(key.spec, presets.PRESETS)))
    elif isinstance(key, ChangeKey):
        scene = _open_change_scene(store, key)
    elif isinstance(key, SelectionKey):
        scene = None
    else:
        scene = _open_commit_scene(store, key)  # a commit's, or one kept under a name
    return scene.with_overlays(*overlays) if scene is not None and overlays else scene


def make_review_key(change: ModelKey) -> ProjectionKey:
    """Return the key of the projection a review of ``change`` is made on (``brief.make_review_spec``)."""
    from nolli.logic.formats import brief

    return projection.make_key(change, brief.make_review_spec())


def add_comments(store: ModelStore, observed: ModelKey, raw: str, by: str, *, verdict_needed: bool = False,
                 dry_run: bool = False) -> tuple[list[dict], list[str]]:
    """Read comments made on the model of ``observed`` from the JSON text ``raw``
    (``comments.make_comments``) and keep them on the model it is a view of
    (``keys.find_base_key``), in place of those ``by`` made on ``observed`` before. Keep nothing
    if any of them is wrong, or with ``dry_run``. Return the comments read and the problems found.
    Raise KeyError if the store cannot open ``observed``."""
    scene = open_scene(store, observed)
    if scene is None:
        raise KeyError(f"no model {observed.format_key()} here to comment on")
    made, errors = comments_lib.make_comments(scene, raw, by, verdict_needed=verdict_needed)
    if not errors and not dry_run:
        base = keys.find_base_key(observed)
        store.save_comments(base, comments_lib.merge_comments(store.load_comments(base), made))
    return made, errors


# -- from the store, or built --------------------------------------------------------------


def _load_maps(store: ModelStore, m: Model) -> Maps:
    return {n: dict(store.open_maps(n).load(m.key.format_key())) for n in store.list_map_names(m.key.format_key())}


def _fetch_commit_model(store: ModelStore, clone: Path, commit: CommitKey, *, run: Run = _run_here,
                       progress: Progress | None = None, rebuild: bool = False) -> tuple[Model, Maps]:
    """Return the model of one commit and its maps, from the store if it holds a fresh one,
    otherwise extracted from ``clone`` (which has the commit)."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m):
        return m, _load_maps(store, m)
    m, maps = run(_extract_commit, clone, commit.sha, commit.repo.format_name(), progress=progress)
    m.key = commit
    return m, maps


def _fetch_whole_model(store: ModelStore, sources: Sources, commit: CommitKey, *, run: Run = _run_here,
                      progress: Progress | None = None, rebuild: bool = False) -> Model:
    """Return a repository's model at one commit from the store, building and storing it if it is
    not there yet."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m):
        return m
    with sources.open_commits(commit.repo, commit.sha) as clone:
        m, maps = _fetch_commit_model(store, clone, commit, run=run, progress=progress, rebuild=True)
    with progress.step("keeping the model"):
        store.save_model(commit, m, maps)
        precompute_scene(store, commit)
    return m


def _fetch_change(store: ModelStore, sources: Sources, change: ChangeKey, *, about: dict | None = None,
                 run: Run = _run_here, progress: Progress | None = None, rebuild: bool = False) -> dict:
    """Return a change's record from the store, or build it and keep it there. A change is built
    from the models of its two commits (taken from the store if it has them) and git's hunks
    between them. ``about`` is what the change says about itself (``pr``, ``title``,
    ``description``, ``budget``) and is kept with it."""
    progress = progress or Quiet()
    kept = None if rebuild else store.load_change(change)
    if kept is not None and is_fresh(kept):
        return kept
    with sources.open_commits(change.repo, change.base, change.head) as clone:
        sides = {side: _fetch_commit_model(store, clone, c, run=run, progress=progress.under(side), rebuild=rebuild)
                 for c, side in zip(change.list_sides(), ("base", "head"), strict=True)}
        content = run(_list_content, clone, change.base, change.head, progress=progress)
    record = {k: v for k, v in (about or {}).items() if v}
    record.update({"base": change.base, "head": change.head, "remote": change.repo.format_remote(), "nolli": _version()})
    with progress.step("keeping the change"):
        meta = _describe_change_meta(sides["base"][0], sides["head"][0], record)
        store.save_change(change, record, sides, {"content": content}, merge.merge_models(change, sides["base"][0], sides["head"][0], meta))
        precompute_scene(store, change)
    return record


# -- extraction: the steps that take time ---------------------------------------------


def _extract_files(repo: Path, config: dict | None = None, progress: Progress | None = None, key: ModelKey = StringKey()) -> tuple[Model, Maps]:
    """Extract the model of the files under ``repo`` as they are (a working tree, or a checkout),
    and return it with what each extractor said about its nodes and edges."""
    found = facts.extract_facts(repo, git.list_tracked_files(repo), config, progress)
    head = git.find_head(repo)
    found.meta.update({"repo": str(repo), "commit": head[:7] if head else None, "nolli": _version()})
    return found.split(key)


def _extract_commit(repo: Path, sha: str, name: str, progress: Progress) -> tuple[Model, Maps]:
    """Extract the model of commit ``sha`` of the git repository ``repo`` from its files, not
    from a working tree, and name its root ``name``."""
    with tempfile.TemporaryDirectory(prefix="nolli-commit-") as tmp:
        with progress.step("checking out the commit"):
            git.export_commit(repo, sha, Path(tmp))
        m, maps = _extract_files(Path(tmp), progress=progress)
    for values in maps.values():  # the root is the temporary directory
        if EntityId.REPO_ROOT in values and "name" in values[EntityId.REPO_ROOT]:
            values[EntityId.REPO_ROOT]["name"] = name
    m.meta.update({"repo": name, "commit": sha[:7]})
    return m, maps


def _list_content(repo: Path, base: str, head: str, progress: Progress) -> dict[Key, dict]:
    """Return the ``content`` map of a change from ``base`` to ``head``. It gives what git says
    changed in each file, keyed by the file's address (its head path; its base path for a deletion)."""
    with progress.step("comparing the commits"):
        return {EntityId.from_path(path): c.to_dict() for path, c in git.list_file_changes(repo, base, head).items()}


def _describe_change_meta(base: Model, head: Model, about: dict) -> dict:
    """Build the ``meta`` of a change's merged model from its sides: the repository, the head
    commit, its files and languages. What the change says about itself (``about``, kept as
    change.json) is not included; it is added as ``meta["change"]`` by ``merge.describe_change``
    when the scene is opened, so that it is read from one place."""
    return {"repo": head.meta.get("repo"), "commit": about.get("head", "")[:7], "files": head.meta.get("files"),
            "languages": {**base.meta.get("languages", {}), **head.meta.get("languages", {})}}


def precompute_scene(store: ModelStore, key: ModelKey) -> None:
    """Compute the overlays a stored model keeps (``_make_kept_overlays``) and save their values in
    the store, so that opening the model reads them instead of computing them. For a change, its
    two commits are precomputed first. Raise KeyError if the store does not have the model."""
    for side in key.list_sides() if isinstance(key, ChangeKey) else ():
        precompute_scene(store, side)
    scene = open_scene(store, key)
    if scene is None:
        raise KeyError(f"no model {key.format_key()} to precompute")
    for o in _make_kept_overlays(store, key):
        precomputed.precompute_overlay_values(store, scene, o)
