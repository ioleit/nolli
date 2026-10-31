"""Python: environment variables read by code, ``use`` from module to ``env:NAME``.

``os.environ["X"]``, ``os.environ.get("X")``, ``os.getenv("X")``.
"""

from __future__ import annotations

import ast

from ...model import USE, Provenance, Relation
from .. import Context
from ..interfaces import ensure_env


def run(ctx: Context) -> None:
    m = ctx.model
    for f in ctx.progress.each(ctx.files):
        rel = ctx.rel(f)
        fid = ctx.fs_id(rel)
        if not rel.endswith(".py") or not m.has(fid) or m.entities[fid].attrs.get("role") == "test":
            continue
        try:
            tree = ast.parse(ctx.read(rel), filename=rel)
        except (SyntaxError, ValueError):
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
