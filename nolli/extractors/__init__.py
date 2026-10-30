"""Extractors turn files into entities and relations.

Each extractor is a function ``run(ctx: Context) -> None`` that adds to
``ctx.model``. They run in the order listed in ``ALL``; the fs extractor runs
first because the others attach to the artifacts it creates. Cross-reference
resolution that needs several extractors' output (import targets, HTTP calls
to routes, hostnames to services) is done afterwards in ``nolli.match``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..model import Model


@dataclass
class Context:
    repo: Path
    model: Model
    files: list[Path]  # tracked, non-ignored files, relative to repo
    config: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)  # scratch space shared between extractors and matchers

    def rel(self, p: Path) -> str:
        return p.as_posix()

    def fs_id(self, rel: str) -> str:
        return "fs:" + (rel if rel else ".")

    def read(self, rel: str) -> bytes:
        return (self.repo / rel).read_bytes()


def all_extractors():
    """Extraction order: the file tree first (everything attaches to it), then per
    language the front-end (imports, symbols) and its adapters, then infrastructure."""
    from . import fs
    from .infra import compose, dockerfile
    from .python import env as py_env, fastapi, imports as py_imports, symbols as py_symbols
    from .typescript import env as ts_env, fetch, imports as ts_imports

    return [
        ("fs", fs.run),
        ("python-imports", py_imports.run),
        ("python-symbols", py_symbols.run),
        ("python-fastapi", fastapi.run),
        ("python-env", py_env.run),
        ("typescript-imports", ts_imports.run),
        ("typescript-fetch", fetch.run),
        ("typescript-env", ts_env.run),
        ("compose", compose.run),
        ("dockerfile", dockerfile.run),
    ]


def describers():
    """Each language's hover vocabulary (``noun``, ``kind``, ``describe``, ``doc``), asked in this order;
    the first that knows an entity answers (see nolli/info.py)."""
    from .python import describe as python
    from .typescript import describe as typescript
    return [python, typescript]
