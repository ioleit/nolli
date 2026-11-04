"""Building models: from a repository's files to what the store keeps (``nolli/datasets.py``
opens them as scenes).

- ``extract``: a directory's files -> its model and, per extractor, what it said (maps);
- ``fetch_commit_model``, ``fetch_change``: from the store, or built from a ``Sources`` and
  kept in the store;

The steps that take time (``extract_commit``, ``list_content``) take paths and return data,
so a caller can run them elsewhere (``run``: the service runs them in processes of their
own); the store and the sources are used only here.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .extractors import Context, all_extractors, language_names, list_symbols_overlays
from .extractors.facts import Facts
from .extractors.match import run_all as match_all
from .keys import ChangeKey, Commit
from .model import Key, Model, address
from .progress import Progress, Quiet
from .store.maps import MapStore
from .sources import Sources
from .sources.git import export_commit, find_head, list_file_changes, list_tracked_files
from .store import ModelStore

WORKING_NOTES = ("py-imports", "js-imports", "py-aliases", "ts-exports")  # extractors' state between passes, not kept

Maps = dict[str, dict[Key, dict]]
Run = Callable[..., Any]  # run(fn, *args, progress=…): fn(*args, progress=…), here or elsewhere


def run_here(fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
    return fn(*args, progress=progress)


def extract(repo: Path, config: dict | None = None, progress: Progress | None = None, key: str = "") -> tuple[Model, Maps]:
    """The model of the files under ``repo`` as they are (a working tree, or a checkout), and
    what each extractor said about its nodes and edges."""
    return extract_facts(repo, config, progress).split(key)


def extract_facts(repo: Path, config: dict | None = None, progress: Progress | None = None) -> Facts:
    """What the extractors found under ``repo``, before it is split into structure and maps
    (``Facts``: entities and relations with their attributes, as the extractors wrote them)."""
    facts = Facts()
    progress = progress or Quiet()
    ctx = Context(repo=repo, model=facts, files=list_tracked_files(repo), config=config or {}, progress=progress)
    timings = {}
    for name, fn in all_extractors():
        t0 = time.time()
        facts.current = name
        with progress.step("file tree" if name == "fs" else name.replace("-", " ")):
            fn(ctx)
        timings[name] = round(time.time() - t0, 3)
    t0 = time.time()
    facts.current = "match"
    with progress.step("matching references"):
        match_all(ctx)
    timings["match"] = round(time.time() - t0, 3)
    head = find_head(repo)
    facts.meta = {"repo": str(repo), "commit": head[:7] if head else None, "files": len(ctx.files), "languages": language_names(),
                  "symbols": list_symbols_overlays(),
                  "timings": timings, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "notes": {k: v for k, v in ctx.notes.items() if k not in WORKING_NOTES}, "nolli": version()}
    return facts


def extract_commit(repo: Path, sha: str, name: str, progress: Progress) -> tuple[Model, Maps]:
    """The model of commit ``sha`` of the git repository ``repo`` (its files, not a working
    tree), its root named ``name``."""
    with tempfile.TemporaryDirectory(prefix="nolli-commit-") as tmp:
        with progress.step("checking out the commit"):
            export_commit(repo, sha, Path(tmp))
        m, maps = extract(Path(tmp), progress=progress)
    for values in maps.values():  # the root is the temporary directory
        if address.REPO_ROOT in values and "name" in values[address.REPO_ROOT]:
            values[address.REPO_ROOT]["name"] = name
    m.meta.update({"repo": name, "commit": sha[:7]})
    return m, maps


def list_content(repo: Path, base: str, head: str, progress: Progress) -> dict[Key, dict]:
    """The ``content`` map of a change: what git says changed in each file, by the file's
    address (its head path; base path for a deletion)."""
    with progress.step("comparing the commits"):
        return {address.repo(path): c.to_dict() for path, c in list_file_changes(repo, base, head).items()}


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


def load_maps(store: MapStore, m: Model) -> Maps:
    return {n: dict(store.open_maps(n).load(m.key)) for n in m.meta.get("overlays") or []}


def fetch_commit_model(store: ModelStore, clone: Path, commit: Commit, *, run: Run = run_here,
                       progress: Progress | None = None, rebuild: bool = False) -> tuple[Model, Maps]:
    """The model of one commit and its extractors' maps: from the store, or extracted from
    ``clone`` (which has the commit)."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m):
        return m, load_maps(store, m)
    m, maps = run(extract_commit, clone, commit.sha, commit.repo.format_name(), progress=progress)
    m.key = commit.format_key()
    return m, maps


def fetch_whole_model(store: ModelStore, sources: Sources, commit: Commit, *, run: Run = run_here,
                      progress: Progress | None = None, rebuild: bool = False) -> Model:
    """A repository at one commit, as the viewer shows a repository as a whole: kept in the store."""
    progress = progress or Quiet()
    m = None if rebuild else store.load_model(commit)
    if m is not None and is_fresh(m):
        return m
    with sources.open_commits(commit.repo, commit.sha) as clone:
        m, maps = fetch_commit_model(store, clone, commit, run=run, progress=progress, rebuild=True)
    with progress.step("keeping the model"):
        store.save_model(commit, m, maps)
    return m


def fetch_change(store: ModelStore, sources: Sources, change: ChangeKey, *, about: dict | None = None,
                 run: Run = run_here, progress: Progress | None = None, rebuild: bool = False) -> dict:
    """A change: from the store, or built from its two commits (their models, from the store
    if it has them) and git's hunks between them, and kept there. ``about``: what the change
    says about itself (``pr``, ``title``, ``description``, ``budget``), kept with it."""
    progress = progress or Quiet()
    kept = None if rebuild else store.load_change(change)
    if kept is not None and is_fresh(kept):
        return kept
    with sources.open_commits(change.repo, change.base, change.head) as clone:
        sides = {side: fetch_commit_model(store, clone, c, run=run, progress=progress.under(side), rebuild=rebuild)
                 for c, side in zip(change.list_sides(), ("base", "head"), strict=True)}
        content = run(list_content, clone, change.base, change.head, progress=progress)
    record = {k: v for k, v in (about or {}).items() if v}
    record.update({"base": change.base, "head": change.head, "remote": change.repo.format_remote(), "nolli": version()})
    with progress.step("keeping the change"):
        store.save_change(change, record, sides, {"content": content})
    return record
