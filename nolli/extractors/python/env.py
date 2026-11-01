"""Python: environment variables read by code, ``use`` from module to ``config:NAME``.

``os.environ["X"]``, ``os.environ.get("X")``, ``os.getenv("X")``.
"""

from __future__ import annotations

import ast

from ...model import USE, Provenance, Relation, address
from .. import Context
from ..interfaces import ensure_env


def run(ctx: Context) -> None:
    m = ctx.model
    for rel in ctx.sources("python", tests=False):
        fid = address.repo(rel)
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
                m.add_relation(Relation(USE, fid, ensure_env(ctx, name, "python-env"), "env", provenance=Provenance("python-env", rel, node.lineno)))
