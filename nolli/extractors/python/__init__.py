"""Python front-end: modules and imports, classes and references, and framework adapters
that read Python syntax (FastAPI/Flask routes, environment reads)."""

from __future__ import annotations

import ast

from .. import Language
from . import describe, env, fastapi, imports, resolve, symbols


def parse(rel: str, source: bytes) -> ast.Module:
    return ast.parse(source, filename=rel)


LANGUAGE = Language(
    name="python",
    extensions={".py": "python"},
    parse=parse,
    extractors=[("python-imports", imports.run), ("python-symbols", symbols.run),
                ("python-fastapi", fastapi.run), ("python-env", env.run)],
    resolvers=[("python-imports", resolve.resolve_imports), ("python-refs", resolve.resolve_refs)],
    describe=describe,
    package_body=("__init__.py",),
    symbols={"class": "C"},
    keep_trees=False,
)
