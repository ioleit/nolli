"""Finds the environment variables that Python code reads.
"""

from __future__ import annotations

import ast

from nolli.extractors import Context, interfaces
from nolli.extractors.facts import Provenance, RelationFact
from nolli.model.schema import EntityId, RelationKind


def run(ctx: Context) -> None:
    """Add a ``use`` from each module to ``config:NAME`` for every variable it reads with
    ``os.environ["X"]``, ``os.environ.get("X")`` or ``os.getenv("X")``."""
    m = ctx.model
    for rel in ctx.sources("python", tests=False):
        fid = EntityId.from_path(rel)
        tree = ctx.tree(rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) in ("os.environ", "environ"):
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    name = node.slice.value
            elif isinstance(node, ast.Call):
                fn = ast.unparse(node.func)
                if fn in ("os.environ.get", "os.getenv", "environ.get", "getenv") and node.args:
                    a = node.args[0]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        name = a.value
            if name:
                m.add_relation(RelationFact(RelationKind.USE, fid, interfaces.ensure_env(ctx, name, "python-env"), "env", provenance=Provenance("python-env", rel, node.lineno)))
