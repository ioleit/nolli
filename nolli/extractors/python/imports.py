"""Python: ``use`` relations from import statements.

Imports are recorded against ``unresolved:py:<module>`` placeholders here;
``nolli.match`` resolves them to files (exact) or external packages.
Relative imports are resolved immediately, since the file's own location
settles them.
"""

from __future__ import annotations

import ast

from ...model import USE, Entity, Provenance, Relation, address
from .. import Context


def module_name_of(rel: str) -> str:
    parts = rel[:-3].split("/") if rel.endswith(".py") else rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def run(ctx: Context) -> None:
    m = ctx.model
    pending: list[tuple[str, str, int]] = []  # (file id, module, line)
    for rel in ctx.sources("python"):
        tree = ctx.tree(rel)
        if tree is None:
            continue
        fid = ctx.repo_id(rel)
        pkg_parts = rel.split("/")[:-1] if not rel.endswith("__init__.py") else rel.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    pending.append((fid, a.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if node.module:
                        pending.append((fid, node.module, node.lineno))
                        # ``from pkg import name`` may name a submodule; record both candidates
                        for a in node.names:
                            pending.append((fid, f"{node.module}.{a.name}", node.lineno))
                else:
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                    mod = ".".join(base + ([node.module] if node.module else []))
                    if mod:
                        pending.append((fid, mod, node.lineno))
                        for a in node.names:
                            pending.append((fid, f"{mod}.{a.name}", node.lineno))
    ctx.notes["py-imports"] = pending
    # Placeholders are created here so the model is complete even before matching.
    for fid, mod, line in pending:
        uid = address.unresolved("py", mod)
        if not m.has(uid):
            m.add_entity(Entity(uid, "unresolved", mod, {"language": "python"}, Provenance("python-imports")))
        m.add_relation(
            Relation(USE, fid, uid, "import", confidence="exact", provenance=Provenance("python-imports", address.path_of(fid), line))
        )
