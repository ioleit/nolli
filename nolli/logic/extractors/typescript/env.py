"""Finds the environment variables that TypeScript/JavaScript reads.
"""

from __future__ import annotations

import re

from nolli.logic.extractors import Context, interfaces
from nolli.logic.extractors.facts import Provenance, RelationFact
from nolli.model.schema import EntityId, RelationKind

JS_ENV = re.compile(rb"(?:process\.env|import\.meta\.env)\.([A-Za-z_][A-Za-z0-9_]*)")


def run(ctx: Context) -> None:
    """Add a ``use`` from each module to ``config:NAME`` for every ``process.env.X`` or
    ``import.meta.env.X`` it reads."""
    m = ctx.model
    for rel in ctx.sources("typescript", tests=False):
        fid = EntityId.from_path(rel)
        src = ctx.read(rel)
        for mo in JS_ENV.finditer(src):
            line = src.count(b"\n", 0, mo.start()) + 1
            m.add_relation(RelationFact(RelationKind.USE, fid, interfaces.ensure_env(ctx, mo.group(1).decode(), "typescript-env"), "env", provenance=Provenance("typescript-env", rel, line)))
