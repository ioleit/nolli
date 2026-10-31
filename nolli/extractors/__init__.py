"""Extractors turn files into entities and relations.

Each extractor is a function ``run(ctx: Context) -> None`` that adds to
``ctx.model``. They run in the order ``all_extractors`` gives; the fs extractor runs
first because the others attach to the artifacts it creates. Cross-reference
resolution that needs several extractors' output (import targets, HTTP calls
to routes, hostnames to services) is done afterwards in ``nolli.match``.

A language is a package next to this file that exports ``LANGUAGE`` (a ``Language``)
and is listed in ``LANGUAGE_PACKAGES``. Everything the rest of nolli knows about a
language comes from there: which files are its modules, how to parse them, its
extractors and resolvers, how the hover text talks about it, and which file is a
folder's own code (``__init__.py``, ``index.ts``).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterator

from ..model import Model
from ..progress import Progress, Quiet


@dataclass
class Context:
    repo: Path
    model: Model
    files: list[Path]  # tracked, non-ignored files, relative to repo
    config: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)  # scratch space shared between extractors and matchers
    progress: Progress = field(default_factory=Quiet)  # nolli/progress.py: ctx.progress.each(files) counts them
    trees: dict = field(default_factory=dict, repr=False)  # rel -> syntax tree (``tree``)

    def rel(self, p: Path) -> str:
        return p.as_posix()

    def fs_id(self, rel: str) -> str:
        return "fs:" + (rel if rel else ".")

    def read(self, rel: str) -> bytes:
        return (self.repo / rel).read_bytes()

    def sources(self, language: str, tests: bool = True) -> Iterator[str]:
        """The files of a language (by its extensions) that the file tree holds, counted
        on ``progress``; ``tests=False`` leaves out files whose role is ``test``."""
        exts = language_named(language).extensions
        for f in self.progress.each(self.files):
            rel = self.rel(f)
            e = self.model.entities.get(self.fs_id(rel))
            if e is None or e.kind != "file" or ext_of(rel) not in exts:
                continue
            if tests or e.attrs.get("role") != "test":
                yield rel

    def tree(self, rel: str):
        """The file's syntax tree from its language's parser; None (and a note under
        ``parse-errors``, once) if it does not parse. Kept for the next extractor if the
        language says so (``Language.keep_trees``)."""
        if rel in self.trees:
            return self.trees[rel]
        lang = language_of(rel)
        if lang is None or lang.parse is None:
            return None
        try:
            tree = lang.parse(rel, self.read(rel))
        except (SyntaxError, ValueError, OSError):
            tree = None
        if tree is None:
            self.notes.setdefault("parse-errors", []).append(rel)
        if lang.keep_trees or tree is None:
            self.trees[rel] = tree
        return tree


@dataclass
class Language:
    """What nolli knows about one language; see the module docstring.

    ``extensions`` maps a file extension to the ``language`` attribute its files get
    (".jsx" -> "javascript"). ``parse(rel, source)`` returns the tree ``Context.tree``
    caches. ``extractors`` and ``resolvers`` are ``(name, run(ctx))`` pairs: extractors
    run after the file tree, resolvers in ``nolli.match`` before the shared ones (APIs).
    ``describe`` is the hover vocabulary (``noun``, ``kind``, ``describe``, ``doc``,
    ``own_code``; see nolli/info.py). ``package_body`` names the files that are their
    folder's own code rather than a module of their own. ``symbols`` are the entity kinds
    the language puts inside its modules, each with its icon. ``keep_trees``: parse each
    file once for all extractors; off for Python, whose ``ast`` trees for a 2,000-file
    repository take 700 MB and make the garbage collector a third of the run time."""
    name: str
    extensions: dict[str, str]
    parse: Callable[[str, bytes], object] | None = None
    extractors: list[tuple[str, Callable]] = field(default_factory=list)
    resolvers: list[tuple[str, Callable]] = field(default_factory=list)
    describe: ModuleType | None = None
    package_body: tuple[str, ...] = ()
    symbols: dict[str, str] = field(default_factory=dict)
    keep_trees: bool = True


LANGUAGE_PACKAGES = ("python", "typescript")


@cache
def languages() -> tuple[Language, ...]:
    return tuple(importlib.import_module(f"{__name__}.{p}").LANGUAGE for p in LANGUAGE_PACKAGES)


def language_named(name: str) -> Language:
    return next(lang for lang in languages() if lang.name == name)


def ext_of(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def language_of(rel: str) -> Language | None:
    ext = ext_of(rel)
    return next((lang for lang in languages() if ext in lang.extensions), None)


@cache
def module_languages() -> frozenset[str]:
    """The ``language`` values whose files are modules (drawn ``m``, unfoldable)."""
    return frozenset(v for lang in languages() for v in lang.extensions.values())


@cache
def package_bodies() -> frozenset[str]:
    return frozenset(n for lang in languages() for n in lang.package_body)


@cache
def symbol_icons() -> dict[str, str]:
    return {k: v for lang in languages() for k, v in lang.symbols.items()}


def all_extractors():
    """Extraction order: the file tree first (everything attaches to it), then per
    language its front-end (imports, symbols) and adapters, then infrastructure, then
    the shared structures the adapters reported into (``api``)."""
    from . import api, fs
    from .infra import compose, dockerfile

    return [("fs", fs.run), *(x for lang in languages() for x in lang.extractors),
            ("compose", compose.run), ("dockerfile", dockerfile.run),
            ("api", api.build)]  # the shared API structure from what the adapters reported


def all_resolvers():
    """Per language its resolvers, in ``languages()`` order (``nolli.match`` runs them)."""
    return [x for lang in languages() for x in lang.resolvers]


def describers():
    """Each language's hover vocabulary, asked in ``languages()`` order; the first that
    knows an entity answers (see nolli/info.py)."""
    return [lang.describe for lang in languages() if lang.describe is not None]
