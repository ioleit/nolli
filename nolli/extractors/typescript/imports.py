"""TypeScript/JavaScript: ``use`` relations from import/require/export-from, marked
``type_only`` for ``import type`` (and imports of types only), ``dynamic`` for ``import()``.

Specifiers are recorded against ``unresolved:js:<specifier>@<file>`` for
relative paths (resolution needs the importing file) and
``unresolved:js:<package>`` for bare specifiers; ``nolli.match`` resolves
them to files (exact) or external packages.
"""

from __future__ import annotations

from tree_sitter_language_pack import get_parser

from ...model import USE, Entity, Provenance, Relation
from .. import Context

GRAMMAR = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
           ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript"}

_parsers: dict[str, object] = {}


def parse(rel: str, source: bytes):
    """A tree-sitter tree: the TSX grammar for .tsx, TypeScript's for the other TS
    extensions, JavaScript's (which reads JSX) for the JS ones."""
    grammar = GRAMMAR["." + rel.rsplit(".", 1)[-1].lower()]
    if grammar not in _parsers:
        _parsers[grammar] = get_parser(grammar)
    return _parsers[grammar].parse(source)


def string_value(node) -> str | None:
    """The literal text of a string node, or None if it has substitutions."""
    if node is None:
        return None
    if node.type == "string":
        frags = [c for c in node.children if c.type == "string_fragment"]
        return b"".join(c.text for c in frags).decode("utf-8", "replace") if frags else ""
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.children):
            return None
        return b"".join(c.text for c in node.children if c.type == "string_fragment").decode("utf-8", "replace")
    return None


def walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def type_only(stmt) -> bool:
    """``import type``/``export type``, or every named import marked ``type``: erased by
    TypeScript, no dependency at run time."""
    if any(c.type == "type" for c in stmt.children):
        return True
    clause = next((c for c in stmt.named_children if c.type == "import_clause"), None)
    if clause is None or any(c.type != "named_imports" for c in clause.named_children):
        return False
    specs = [s for c in clause.named_children for s in c.named_children if s.type == "import_specifier"]
    return bool(specs) and all(any(t.type == "type" for t in s.children) for s in specs)


def specifiers(tree) -> list[tuple[str, int, dict]]:
    """(specifier, line, attrs) of every import, re-export, ``require`` and ``import()``:
    ``type_only`` (see ``type_only``), ``dynamic`` for ``import()``, loaded when the code
    runs (often a lazily loaded part of a frontend or a command line)."""
    out: list[tuple[str, int, dict]] = []
    for n in walk(tree.root_node):
        if n.type in ("import_statement", "export_statement"):
            src = n.child_by_field_name("source")
            v = string_value(src)
            if v:
                out.append((v, n.start_point[0] + 1, {"type_only": True} if type_only(n) else {}))
        elif n.type == "call_expression":
            fn = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if fn is not None and args is not None and fn.text in (b"require", b"import"):
                first = next((c for c in args.children if c.type in ("string", "template_string")), None)
                v = string_value(first)
                if v:
                    out.append((v, n.start_point[0] + 1, {"dynamic": True} if fn.text == b"import" else {}))
    return out


def run(ctx: Context) -> None:
    m = ctx.model
    pending: list[tuple[str, str, int]] = []
    for rel in ctx.sources("typescript"):
        tree = ctx.tree(rel)
        if tree is None:
            continue
        fid = ctx.fs_id(rel)
        for spec, line, attrs in specifiers(tree):
            pending.append((fid, spec, line, attrs))
    ctx.notes["js-imports"] = pending
    for fid, spec, line, attrs in pending:
        relative = spec.startswith(".") or spec.startswith("/")
        uid = f"unresolved:js:{spec}@{fid[3:]}" if relative else f"unresolved:js:{spec}"
        if not m.has(uid):
            m.add_entity(Entity(uid, "unresolved", spec, {"language": "javascript"}, Provenance("ts-imports")))
        m.add_relation(Relation(USE, fid, uid, "import", provenance=Provenance("ts-imports", fid[3:], line),
                                attrs=attrs))
