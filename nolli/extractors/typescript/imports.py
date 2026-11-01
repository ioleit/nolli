"""TypeScript/JavaScript: ``use`` relations from import/require/export-from, marked
``type_only`` for ``import type`` (and imports of types only), ``dynamic`` for ``import()``.

Specifiers are recorded against ``unresolved:js:<specifier>@<file>`` for
relative paths (resolution needs the importing file) and
``unresolved:js:<package>`` for bare specifiers; ``nolli.match`` resolves
them to files (exact) or external packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter_language_pack import get_parser

from ... import address
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


@dataclass
class Import:
    """One import, re-export, ``require`` or ``import()`` of a module, as both the import
    edges (``run``) and the symbol references (``symbols.py``) read it.

    ``names``: what it binds, (local name, name there, type only), the name there being
    ``default`` for a default import and ``*`` for a namespace (``import * as ns``,
    ``const ns = require(...)``). ``exports``: what an ``export ... from`` re-exports,
    (exported name, name there): ``("*", "*")`` for ``export *``, ``(ns, "")`` for
    ``export * as ns``. ``type_only``: the whole statement is erased (``import type``,
    ``export type``, or every named import marked ``type``). ``top``: it is at the top
    level of the module, so its names are bound in the whole file."""
    spec: str
    line: int
    names: list[tuple[str, str, bool]] = field(default_factory=list)
    exports: list[tuple[str, str]] = field(default_factory=list)
    type_only: bool = False
    dynamic: bool = False
    top: bool = False


def has_type(node) -> bool:
    """The ``type`` keyword on a statement or a specifier (``import type``, ``{ type X }``)."""
    return any(c.type == "type" for c in node.children)


def at_top(n, root) -> bool:
    """A statement directly in the module. Compared with the root itself: a file the
    parser cannot read whole has an ``ERROR`` root, not ``program``."""
    return n is not None and n.parent is not None and n.parent.id == root.id


def from_statement(n, root) -> Import | None:
    """``import ... from 'x'`` or ``export ... from 'x'``."""
    spec = string_value(n.child_by_field_name("source"))
    if not spec:
        return None
    imp = Import(spec, n.start_point[0] + 1, top=at_top(n, root))
    stmt_type = has_type(n)
    for c in n.named_children:
        if c.type == "import_clause":
            for b in c.named_children:
                if b.type == "identifier":
                    imp.names.append((b.text.decode(), "default", stmt_type))
                elif b.type == "namespace_import":
                    ident = next((i for i in b.named_children if i.type == "identifier"), None)
                    if ident is not None:
                        imp.names.append((ident.text.decode(), "*", stmt_type))
                elif b.type == "named_imports":
                    for sp in b.named_children:
                        name, alias = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                        if sp.type == "import_specifier" and name is not None:
                            imp.names.append(((alias or name).text.decode(), name.text.decode().strip("'\""),
                                              stmt_type or has_type(sp)))
        elif c.type == "export_clause":
            for sp in c.named_children:
                name, alias = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                if sp.type == "export_specifier" and name is not None:
                    imp.exports.append(((alias or name).text.decode(), name.text.decode()))
        elif c.type == "namespace_export":
            ident = next((i for i in c.named_children if i.type == "identifier"), None)
            if ident is not None:
                imp.exports.append((ident.text.decode(), ""))
    if n.type == "export_statement" and not imp.exports:
        imp.exports.append(("*", "*"))
    imp.type_only = stmt_type or (bool(imp.names) and all(t for _, _, t in imp.names))
    return imp


def from_call(n, root) -> Import | None:
    """``require('x')`` (bound by ``const x = ...`` or ``const {a, b: c} = ...``) or ``import('x')``."""
    fn, args = n.child_by_field_name("function"), n.child_by_field_name("arguments")
    if fn is None or args is None or fn.text not in (b"require", b"import"):
        return None
    spec = string_value(next((c for c in args.children if c.type in ("string", "template_string")), None))
    if not spec:
        return None
    imp = Import(spec, n.start_point[0] + 1, dynamic=fn.text == b"import")
    decl = n.parent
    if fn.text == b"require" and decl is not None and decl.type == "variable_declarator" and decl.child_by_field_name("value") == n:
        target = decl.child_by_field_name("name")
        if target is not None and target.type == "identifier":
            imp.names.append((target.text.decode(), "*", False))
        elif target is not None and target.type == "object_pattern":
            for pt in target.named_children:
                if pt.type == "shorthand_property_identifier_pattern":
                    imp.names.append((pt.text.decode(), pt.text.decode(), False))
                elif pt.type == "pair_pattern":
                    k, v = pt.child_by_field_name("key"), pt.child_by_field_name("value")
                    if k is not None and v is not None and v.type == "identifier":
                        imp.names.append((v.text.decode(), k.text.decode(), False))
        imp.top = at_top(decl.parent, root)
    return imp


def read(tree) -> list[Import]:
    """Every import of a module, in source order."""
    out: list[Import] = []
    for n in walk(tree.root_node):
        imp = from_statement(n, tree.root_node) if n.type in ("import_statement", "export_statement") else \
            from_call(n, tree.root_node) if n.type == "call_expression" else None
        if imp is not None:
            out.append(imp)
    return out


def run(ctx: Context) -> None:
    m = ctx.model
    pending: list[tuple[str, str, int]] = []
    for rel in ctx.sources("typescript"):
        tree = ctx.tree(rel)
        if tree is None:
            continue
        fid = ctx.repo_id(rel)
        for imp in read(tree):
            attrs = {"type_only": True} if imp.type_only else {"dynamic": True} if imp.dynamic else {}
            pending.append((fid, imp.spec, imp.line, attrs))
    ctx.notes["js-imports"] = pending
    for fid, spec, line, attrs in pending:
        relative = spec.startswith(".") or spec.startswith("/")
        uid = address.unresolved("js", f"{spec}@{address.path_of(fid)}" if relative else spec)
        if not m.has(uid):
            m.add_entity(Entity(uid, "unresolved", spec, {"language": "javascript"}, Provenance("ts-imports")))
        m.add_relation(Relation(USE, fid, uid, "import", provenance=Provenance("ts-imports", address.path_of(fid), line),
                                attrs=attrs))
