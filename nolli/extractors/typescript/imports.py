"""TypeScript/JavaScript: ``use`` relations from import/require/export-from.

Specifiers are recorded against ``unresolved:js:<specifier>@<file>`` for
relative paths (resolution needs the importing file) and
``unresolved:js:<package>`` for bare specifiers; ``nolli.match`` resolves
them to files (exact) or external packages.
"""

from __future__ import annotations

from tree_sitter_language_pack import get_parser

from ...model import USE, Entity, Provenance, Relation
from .. import Context

PARSER_BY_LANG = {"typescript": None, "javascript": None}
EXT_LANG = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript"}

_parsers: dict[str, object] = {}


def parser_for(rel: str):
    ext = "." + rel.rsplit(".", 1)[-1] if "." in rel else ""
    lang = EXT_LANG.get(ext)
    if not lang:
        return None
    if lang not in _parsers:
        _parsers[lang] = get_parser(lang)
    return _parsers[lang]


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


def specifiers(tree) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for n in walk(tree.root_node):
        if n.type in ("import_statement", "export_statement"):
            src = n.child_by_field_name("source")
            v = string_value(src)
            if v:
                out.append((v, n.start_point[0] + 1))
        elif n.type == "call_expression":
            fn = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if fn is not None and args is not None and fn.text in (b"require", b"import"):
                first = next((c for c in args.children if c.type in ("string", "template_string")), None)
                v = string_value(first)
                if v:
                    out.append((v, n.start_point[0] + 1))
    return out


def run(ctx: Context) -> None:
    m = ctx.model
    pending: list[tuple[str, str, int]] = []
    for f in ctx.files:
        rel = ctx.rel(f)
        p = parser_for(rel)
        if p is None or not m.has(ctx.fs_id(rel)):
            continue
        try:
            tree = p.parse(ctx.read(rel))
        except Exception:
            ctx.notes.setdefault("parse-errors", []).append(rel)
            continue
        fid = ctx.fs_id(rel)
        for spec, line in specifiers(tree):
            pending.append((fid, spec, line))
    ctx.notes["js-imports"] = pending
    for fid, spec, line in pending:
        relative = spec.startswith(".") or spec.startswith("/")
        uid = f"unresolved:js:{spec}@{fid[3:]}" if relative else f"unresolved:js:{spec}"
        if not m.has(uid):
            m.add_entity(Entity(uid, "unresolved", spec, {"language": "javascript"}, Provenance("ts-imports")))
        m.add_relation(Relation(USE, fid, uid, "import", provenance=Provenance("ts-imports", fid[3:], line)))
