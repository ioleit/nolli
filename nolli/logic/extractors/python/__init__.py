"""The Python language: its parser, extractors and resolvers.

The package holds what reads Python syntax: modules and imports, classes and the
references between them, and adapters for frameworks (web routes, environment reads)."""

from __future__ import annotations

import ast

from nolli.logic.extractors import Language
from nolli.logic.extractors.python import env, fastapi, imports, resolve, symbols


def _parse(rel: str, source: bytes) -> ast.Module:
    return ast.parse(source, filename=rel)


LANGUAGE = Language(
    name="python",
    extensions={".py": "python"},
    parse=_parse,
    extractors=[("python-imports", imports.run), ("python-symbols", symbols.run),
                ("python-fastapi", fastapi.run), ("python-env", env.run)],
    resolvers=[("python-imports", resolve.resolve_imports), ("python-refs", resolve.resolve_refs)],
    names={"python": "Python"},
    package_body=("__init__.py",),
    package="package",
    keep_trees=False,
)
