"""Builds models from a repository's files and keeps them in a store.

A model or change is taken from the store when it has one built by this version, else it is
extracted from the repository's commits and kept there.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nolli import scenes
from nolli.logic import extractors
from nolli.logic.extractors import Context, match
from nolli.logic.extractors.facts import Facts
from nolli.logic.sources import Sources, git
from nolli.logic.transformations import merge
from nolli.model.keys import ChangeKey, CommitKey, ModelKey, StringKey
from nolli.model.model import Key, Model
from nolli.model.schema import EntityId
from nolli.progress import Progress, Quiet
from nolli.store import ModelStore
from nolli.store.maps import Maps, MapStore

WORKING_NOTES = ("py-imports", "js-imports", "py-aliases", "ts-exports")  # extractors' state between passes, not kept

# run(fn, *args, progress=…) calls fn(*args, progress=…), here or elsewhere (in a process of
# its own). The steps that take time (extract_commit, list_content) take paths and return
# data, so that a caller can run them elsewhere.
Run = Callable[..., Any]


def _run_here(fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
    return fn(*args, progress=progress)


def extract(repo: Path, config: dict | None = None, progress: Progress | None = None, key: ModelKey = StringKey()) -> tuple[Model, Maps]:
    """The model of the files under ``repo`` as they are (a working tree, or a checkout), and
    what each extractor said about its nodes and edges."""
    return _extract_facts(repo, config, progress).split(key)


def _extract_facts(repo: Path, config: dict | None = None, progress: Progress | None = None) -> Facts:
    """What the extractors found under ``repo``, before it is split into structure and maps
    (``Facts``: entities and relations with their attributes, as the extractors wrote them)."""
    facts = Facts()
    progress = progress or Quiet()
    ctx = Context(repo=repo, model=facts, files=git.list_tracked_files(repo), config=config or {}, progress=progress)
    timings = {}
    for name, fn in extractors.all_extractors():
        t0 = time.time()
        facts.current = name
        with progress.step("file tree" if name == "fs" else name.replace("-", " ")):
            fn(ctx)
        timings[name] = round(time.time() - t0, 3)
    t0 = time.time()
    facts.current = "match"
    with progress.step("matching references"):
        match.run_all(ctx)
    timings["match"] = round(time.time() - t0, 3)
    head = git.find_head(repo)
    facts.meta = {"repo": str(repo), "commit": head[:7] if head else None, "files": len(ctx.files), "languages": extractors.language_names(),
                  "timings": timings, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "notes": {k: v for k, v in ctx.notes.items() if k not in WORKING_NOTES}, "nolli": version()}
    return facts


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
