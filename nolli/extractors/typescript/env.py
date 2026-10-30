"""TypeScript/JavaScript: environment variables read by code, ``use`` from module to ``env:NAME``.

``process.env.X``, ``import.meta.env.X``.
"""

from __future__ import annotations

import re

from ...model import USE, Provenance, Relation
from .. import Context
from ..interfaces import ensure_env
from .imports import parser_for

JS_ENV = re.compile(rb"(?:process\.env|import\.meta\.env)\.([A-Za-z_][A-Za-z0-9_]*)")


def run(ctx: Context) -> None:
    m = ctx.model
    for f in ctx.files:
        rel = ctx.rel(f)
        fid = ctx.fs_id(rel)
        if parser_for(rel) is None or not m.has(fid) or m.entities[fid].attrs.get("role") == "test":
            continue
        src = ctx.read(rel)
        for mo in JS_ENV.finditer(src):
            line = src.count(b"\n", 0, mo.start()) + 1
            m.add_relation(Relation(USE, fid, ensure_env(ctx, mo.group(1).decode(), "typescript-env"), "env", provenance=Provenance("typescript-env", rel, line)))
