"""TypeScript/JavaScript: classes and types as entities inside modules, and the
references between them.

Each top-level ``class``, ``interface``, ``type`` alias and ``enum`` (exported or not;
``abstract``, ``const enum`` and ``declare`` forms included) becomes an entity
``ts:<file>#<Name>`` of that kind, contained in its module. In TypeScript the interfaces
and type aliases are what Python's data classes are: the shapes modules agree on, so
they are atoms like classes. Functions are not entities, components included: a
module's functions and its top-level code are the module's own body, as in Python (the
picture is about architecture, not call graphs). A declaration merged from several
statements (an interface declared twice, a class and an interface of one name) is one
entity, at its first statement.

References are ``use`` relations with subkind ``ref`` (attrs ``name``: the name used),
from the symbol whose statement mentions a name (or from the module, for anything
else at the top level) to what the name is bound to: a symbol of the same file, or an
import, recorded against ``unresolved:tsref:<specifier>@<file>|<name>`` for
``resolve.resolve_refs`` (``<name>`` is ``default`` for a default import and empty
for a namespace used whole). ``ns.Name`` through a namespace import (``import * as ns``,
``const ns = require(...)``) refers to ``Name``. A reference is ``type_only`` when
every mention is in a type position or the import is ``import type``: TypeScript
erases it, so it is no dependency at run time (and a cycle through it none either).

For the hover text a module also gets ``functions``, ``constants`` (UPPER_CASE),
``own_lines`` and, for a package body (``index.ts``), ``reexports``; its leading JSDoc
comment is its ``doc``, as a symbol's is. ``ts-exports`` in the notes holds each
file's re-exports for the resolver to follow.
"""

from __future__ import annotations

from collections import Counter

from ...model import USE, Entity, Provenance, Relation
from .. import Context
from .imports import read

EXTRACTOR = "typescript-symbols"
DECLS = {"class_declaration": "class", "abstract_class_declaration": "class", "class": "class",
         "interface_declaration": "interface", "type_alias_declaration": "type", "enum_declaration": "enum"}
FUNCTION_VALUES = ("arrow_function", "function_expression", "function", "generator_function")
DOC_MAX = 240  # characters of a doc comment's summary kept
NAMES_MAX = 60  # names kept per list (functions, constants, re-exports)


def declaration(stmt):
    """The declaration a top-level statement makes (unwrapping ``export``, ``export
    default`` and ``declare``), or the statement itself."""
    if stmt.type == "export_statement":
        d = stmt.child_by_field_name("declaration") or next((c for c in stmt.named_children if c.type in DECLS), None)
        return d if d is not None else stmt
    if stmt.type == "ambient_declaration":
        return next((c for c in stmt.named_children if c.type in DECLS or c.type in ("function_signature", "lexical_declaration")), stmt)
    return stmt


def decl_name(d) -> str | None:
    n = d.child_by_field_name("name")
    return n.text.decode() if n is not None else None


def is_doc(n) -> bool:
    return n is not None and n.type == "comment" and n.text.startswith(b"/**")


def doc_comment(stmt) -> str | None:
    """The ``/** ... */`` comment right before a statement."""
    prev = stmt.prev_sibling
    return doc_text(prev) if is_doc(prev) and stmt.start_point[0] - prev.end_point[0] <= 1 else None


def file_doc(root) -> str | None:
    """A file's leading ``/** ... */`` comment, unless it belongs to the declaration right
    below it."""
    first = root.named_children[0] if root.named_children else None
    if not is_doc(first):
        return None
    nxt = first.next_named_sibling
    if nxt is not None and nxt.start_point[0] - first.end_point[0] <= 1 and nxt.type not in ("import_statement", "comment"):
        return None
    return doc_text(first)


def doc_text(comment) -> str | None:
    """A doc comment's first paragraph on one line, without the ``@tags``, cut at DOC_MAX
    characters."""
    lines = [ln.strip().lstrip("*").strip() for ln in comment.text.decode("utf-8", "replace")[3:-2].splitlines()]
    para: list[str] = []
    for ln in lines:
        if ln.startswith("@") or (not ln and para):
            break
        if ln:
            para.append(ln)
    text = " ".join(para)
    if not text:
        return None
    return text if len(text) <= DOC_MAX else text[: DOC_MAX - 1].rsplit(" ", 1)[0] + "…"


def local_exports(stmt) -> list[tuple[str, str]]:
    """``export { a as b }`` and ``export default a`` without a source: (exported, local)."""
    if stmt.child_by_field_name("source") is not None:
        return []
    out = []
    for c in stmt.named_children:
        if c.type == "export_clause":
            for s in c.named_children:
                name, alias = s.child_by_field_name("name"), s.child_by_field_name("alias")
                if s.type == "export_specifier" and name is not None:
                    out.append(((alias or name).text.decode(), name.text.decode()))
    if any(c.type == "default" for c in stmt.children):
        value = stmt.child_by_field_name("value") or next((c for c in stmt.named_children if c.type == "identifier"), None)
        if value is not None and value.type == "identifier":
            out.append(("default", value.text.decode()))
    return out


def references(node, bindings: dict[str, tuple]) -> dict[tuple, list[int]]:
    """binding -> [mentions, mentions in a value position] in a subtree."""
    found: dict[tuple, list[int]] = {}

    def add(b: tuple, value: bool) -> None:
        f = found.setdefault(b, [0, 0])
        f[0] += 1
        f[1] += value

    stack = [node]
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("comment", "string"):
            continue
        if t in ("member_expression", "nested_type_identifier"):
            obj = n.named_children[0] if n.named_children else None
            prop = n.named_children[-1] if len(n.named_children) > 1 else None
            b = bindings.get(obj.text.decode()) if obj is not None and obj.type == "identifier" else None
            if b is not None and b[0] == "module" and prop is not None and prop.type in ("property_identifier", "type_identifier"):
                add(("chain", b[1], prop.text.decode(), b[2]), t == "member_expression")
                continue
        elif t in ("identifier", "type_identifier", "shorthand_property_identifier"):
            b = bindings.get(n.text.decode())
            if b is not None:
                add(b, t != "type_identifier")
            continue
        stack.extend(n.named_children)
    return found


def own_code(root, is_body: bool, symbol_lines: int, total_lines: int | None, reexports: list[str]) -> dict:
    """What a module holds outside its classes and types, for the hover text on its
    own-code dot: top-level functions (declarations and ``const f = () => ...``) and
    constants (UPPER_CASE) in source order, the lines outside its symbols, and for a
    package body (``index.ts``) the names it re-exports."""
    functions: list[str] = []
    constants: list[str] = []
    for stmt in root.named_children:
        d = declaration(stmt)
        if d.type in ("function_declaration", "generator_function_declaration", "function_signature"):
            name = decl_name(d)
            if name and name not in functions:
                functions.append(name)
        elif d.type in ("lexical_declaration", "variable_declaration"):
            for v in d.named_children:
                if v.type != "variable_declarator":
                    continue
                name, value = v.child_by_field_name("name"), v.child_by_field_name("value")
                if name is None or name.type != "identifier":
                    continue
                n = name.text.decode()
                if value is not None and value.type in FUNCTION_VALUES:
                    functions.append(n)
                elif n.isupper() and n not in constants:
                    constants.append(n)
    out: dict = {}
    if functions:
        out["functions"] = functions[:NAMES_MAX]
    if constants:
        out["constants"] = constants[:NAMES_MAX]
    if total_lines is not None:
        out["own_lines"] = max(0, total_lines - symbol_lines)
    if reexports and is_body:
        out["reexports"] = reexports[:NAMES_MAX]
    return out


def run(ctx: Context) -> None:
    m = ctx.model
    exports: dict[str, dict] = {}
    pending: Counter[tuple[str, tuple]] = Counter()  # (src entity, binding) -> mentions
    values: Counter[tuple[str, tuple]] = Counter()  # ... of which in value positions
    for rel in ctx.sources("typescript"):
        tree = ctx.tree(rel)
        if tree is None:
            continue
        fid = ctx.fs_id(rel)
        lang = m.entities[fid].attrs.get("language", "typescript")
        root = tree.root_node
        doc = file_doc(root)
        if doc:
            m.entities[fid].attrs["doc"] = doc

        bindings: dict[str, tuple] = {}
        symbols: dict[str, str] = {}
        by_stmt: dict[int, str] = {}
        reexports: list[tuple[str, str, str]] = []
        local: list[tuple[str, str]] = []
        symbol_lines = 0
        for imp in read(tree):
            if imp.exports:
                reexports += [(exported, imp.spec, there) for exported, there in imp.exports]
            if not imp.top:
                continue
            for local_name, there, type_only in imp.names:
                bindings[local_name] = ("module", imp.spec, type_only) if there == "*" else \
                    ("from", imp.spec, there, type_only, local_name)
        for stmt in root.named_children:
            if stmt.type == "import_statement":
                continue
            d = declaration(stmt)
            if d.type in ("lexical_declaration", "variable_declaration"):
                for v in d.named_children:  # ``const f = () => ...`` is a function of the module
                    value = v.child_by_field_name("value") if v.type == "variable_declarator" else None
                    if value is not None and value.type in FUNCTION_VALUES and v.child_by_field_name("name").type == "identifier":
                        bindings[v.child_by_field_name("name").text.decode()] = ("local", fid, v.child_by_field_name("name").text.decode())
            if stmt.type == "export_statement":
                if stmt.child_by_field_name("source") is not None:
                    continue
                local += local_exports(stmt)
            kind = DECLS.get(d.type)
            name = decl_name(d) if kind else None
            if not name:
                if d.type in ("function_declaration", "generator_function_declaration") and decl_name(d):
                    bindings[decl_name(d)] = ("local", fid, decl_name(d))  # a module's functions are its own body
                continue
            if stmt.type == "export_statement" and any(c.type == "default" for c in stmt.children):
                local.append(("default", name))
            sid = f"ts:{rel}#{name}"
            if name not in symbols:
                attrs = {"path": rel, "line": stmt.start_point[0] + 1, "end_line": stmt.end_point[0] + 1, "language": lang,
                         "exported": stmt.type == "export_statement"}
                doc = doc_comment(stmt)
                if doc:
                    attrs["doc"] = doc
                m.add_entity(Entity(sid, kind, name, attrs, Provenance(EXTRACTOR, rel, stmt.start_point[0] + 1)))
                m.contain(fid, sid, EXTRACTOR)
                symbols[name] = sid
            symbol_lines += stmt.end_point[0] - stmt.start_point[0] + 1
            by_stmt[stmt.id] = symbols[name]
        for name, sid in symbols.items():
            bindings[name] = ("local", sid, name)
        own = own_code(root, bool(m.entities[fid].attrs.get("body")), symbol_lines, m.entities[fid].attrs.get("lines"),
                       [e for e, _, _ in reexports if e != "*"])
        m.entities[fid].attrs.update(own)
        names = set(symbols) | {k for k, b in bindings.items() if b[0] == "local"} | set(own.get("constants", []))
        exports[rel] = {"reexports": reexports, "local": dict(local), "names": names}

        for stmt in root.named_children:
            if stmt.type == "import_statement" or (stmt.type == "export_statement" and stmt.child_by_field_name("source") is not None):
                continue
            src = by_stmt.get(stmt.id, fid)
            for b, (n, v) in references(stmt, bindings).items():
                if b[0] == "local" and b[1] == src:
                    continue
                pending[(src, b)] += n
                values[(src, b)] += v

    ctx.notes["ts-exports"] = exports
    for (src, b), n in pending.items():
        file = (src.split("#")[0][3:] if src.startswith("ts:") else src[3:])
        if b[0] == "local":
            m.add_relation(Relation(USE, src, b[1], "ref", n, "exact", Provenance(EXTRACTOR, file),
                                    {"name": b[2], "type_only": values[(src, b)] == 0, "language": "javascript"}))
            continue
        spec, name, type_only = (b[1], "", b[2]) if b[0] == "module" else (b[1], b[2], b[3])
        shown = b[4] if name == "default" else name or spec.rsplit("/", 1)[-1]  # a default import by its local name
        type_only = type_only or values[(src, b)] == 0
        key = f"{spec}@{file}|{name}"
        uid = f"unresolved:tsref:{key}"
        if not m.has(uid):
            m.add_entity(Entity(uid, "unresolved", f"{spec} {name}".strip(),
                                {"language": "javascript", "ref": True, "spec": spec, "from": file, "name": name}, Provenance(EXTRACTOR)))
        m.add_relation(Relation(USE, src, uid, "ref", n, "exact", Provenance(EXTRACTOR, file),
                                {"name": shown, "type_only": type_only, "language": "javascript"}))

