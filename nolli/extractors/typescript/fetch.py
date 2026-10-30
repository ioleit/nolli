"""HTTP calls made by frontend code (TypeScript/JavaScript).

Two passes over the frontend's syntax trees:

1. Direct calls: ``fetch(<url>)``, ``axios.<method>(<url>)``,
   ``new EventSource(<url>)``, ``new WebSocket(<url>)``. The URL's static
   shape is kept (template substitutions become ``*`` segments; a leading
   substitution such as ``${API_BASE}`` is dropped).
2. One level of indirection: a function whose body passes one of its
   parameters into such a URL is a *helper*; every call to a function of that
   name elsewhere contributes its argument as a URL.

Calls are reported to the shared API structure (``extractors/api.py``) as
``unresolved:http:<METHOD or ?> <pattern>`` placeholders; ``nolli.match`` binds them
to endpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import Context, api
from .imports import parser_for, walk

AXIOS_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass
class Call:
    file: str
    line: int
    method: str
    pattern: str
    via: str  # "direct" or helper name


def module_constants(root) -> dict[str, str]:
    """Top-level ``const NAME = "literal"`` (also ``x || "literal"``), for folding ``${API_BASE}``."""
    consts: dict[str, str] = {}

    def literal(n) -> str | None:
        if n is None:
            return None
        if n.type in ("string", "template_string"):
            v = url_pattern(n, {})
            return v if v is not None and "\x00" not in v else None
        if n.type == "binary_expression" and n.child_by_field_name("operator") is not None and n.child_by_field_name("operator").text in (b"||", b"??"):
            return literal(n.child_by_field_name("right")) or literal(n.child_by_field_name("left"))
        if n.type == "parenthesized_expression" and n.named_children:
            return literal(n.named_children[0])
        return None

    for n in root.children:
        decl = n.child_by_field_name("declaration") if n.type == "export_statement" else n
        if decl is None or decl.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for d in decl.named_children:
            if d.type == "variable_declarator":
                name, value = d.child_by_field_name("name"), d.child_by_field_name("value")
                if name is not None and name.type == "identifier":
                    v = literal(value)
                    if v is not None:
                        consts[name.text.decode()] = v
    return consts


def url_pattern(node, consts: dict[str, str] | None = None) -> str | None:
    """Static shape of a URL expression: '/api/x/*' or None if nothing static.
    Substitutions of known module constants are folded in; others become \\x00."""
    consts = consts or {}
    if node is None:
        return None
    if node.type == "string":
        return b"".join(c.text for c in node.children if c.type == "string_fragment").decode("utf-8", "replace")
    if node.type == "identifier":
        return consts.get(node.text.decode())
    if node.type == "template_string":
        parts = []
        for c in node.children:
            if c.type == "string_fragment":
                parts.append(c.text.decode("utf-8", "replace"))
            elif c.type == "template_substitution":
                inner = c.named_children[0] if c.named_children else None
                v = consts.get(inner.text.decode()) if inner is not None and inner.type == "identifier" else None
                parts.append(v if v is not None else "\x00")
        s = "".join(parts)
        return s if s.strip("\x00") else None
    if node.type == "binary_expression":
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        lp, rp = url_pattern(left, consts), url_pattern(right, consts)
        return (lp or "\x00") + (rp or "\x00")
    if node.type == "parenthesized_expression" and node.named_children:
        return url_pattern(node.named_children[0], consts)
    return None


def static_prefix(node, param: str, consts: dict[str, str]) -> str:
    """Static text of a URL template before the substitution that carries ``param``."""
    if node is None or node.type != "template_string":
        return ""
    parts: list[str] = []
    for c in node.children:
        if c.type == "string_fragment":
            parts.append(c.text.decode("utf-8", "replace"))
        elif c.type == "template_substitution":
            inner = c.named_children[0] if c.named_children else None
            if inner is not None and inner.text.decode() == param:
                break
            v = consts.get(inner.text.decode()) if inner is not None and inner.type == "identifier" else None
            parts.append(v if v is not None else "\x00")
    return "".join(parts)


def normalize(p: str) -> str | None:
    p = p.split("?")[0]
    p = re.sub(r"^\x00+", "", p)  # leading ${BASE}
    p = re.sub(r"^https?://[^/]*", "", p)
    if not p.startswith("/"):
        return None
    p = re.sub(r"\x00+", "*", p)
    p = re.sub(r"/\*[^/]*", "/*", p)  # '/x/*suffix' -> '/x/*'
    p = re.sub(r"[^/]*\*", "*", p)
    return p.rstrip("/") or "/"


def method_of(call_node, default: str) -> str:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return default
    for c in args.named_children[1:2]:
        if c.type == "object":
            for pair in c.named_children:
                if pair.type == "pair":
                    k = pair.child_by_field_name("key")
                    v = pair.child_by_field_name("value")
                    if k is not None and k.text.strip(b"'\"") == b"method" and v is not None and v.type == "string":
                        return v.text.strip(b"'\"`").decode().upper()
    return default


def enclosing_function(node):
    n = node.parent
    while n is not None:
        if n.type in ("function_declaration", "method_definition"):
            name = n.child_by_field_name("name")
            return n, (name.text.decode() if name is not None else None)
        if n.type in ("arrow_function", "function_expression", "function"):
            p = n.parent
            if p is not None and p.type == "variable_declarator":
                name = p.child_by_field_name("name")
                return n, (name.text.decode() if name is not None else None)
            if p is not None and p.type == "pair":
                k = p.child_by_field_name("key")
                return n, (k.text.decode().strip("'\"") if k is not None else None)
            return n, None
        n = n.parent
    return None, None


def param_names(fn_node) -> list[str]:
    params = fn_node.child_by_field_name("parameters") or fn_node.child_by_field_name("parameter")
    if params is None:
        return []
    out = []
    for c in params.named_children if params.type != "identifier" else [params]:
        if c.type == "identifier":
            out.append(c.text.decode())
        elif c.type in ("required_parameter", "optional_parameter"):
            pat = c.child_by_field_name("pattern")
            if pat is not None and pat.type == "identifier":
                out.append(pat.text.decode())
            else:
                out.append("?")
        else:
            out.append("?")
    return out


def substitution_idents(node) -> set[str]:
    out = set()
    for n in walk(node):
        if n.type == "template_substitution":
            for c in walk(n):
                if c.type == "identifier":
                    out.add(c.text.decode())
        if n.type == "identifier" and node.type == "binary_expression":
            out.add(n.text.decode())
    return out


def run(ctx: Context) -> None:
    m = ctx.model
    trees: dict[str, object] = {}
    for f in ctx.files:
        rel = ctx.rel(f)
        p = parser_for(rel)
        if p is None or not m.has(ctx.fs_id(rel)) or m.entities[ctx.fs_id(rel)].attrs.get("role") == "test":
            continue
        trees[rel] = p.parse(ctx.read(rel))

    calls: list[Call] = []
    helpers: dict[str, tuple[int, str]] = {}  # helper name -> (parameter index carrying the URL, static prefix)
    consts_by_file = {rel: module_constants(tree.root_node) for rel, tree in trees.items()}

    for rel, tree in trees.items():
        consts = consts_by_file[rel]
        for n in walk(tree.root_node):
            url_node, default_method, is_call = None, "?", False
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                if fn is None or args is None:
                    continue
                ft = fn.text.decode()
                if ft == "fetch" or ft.endswith(".fetch"):
                    url_node, default_method, is_call = (args.named_children[0] if args.named_children else None), method_of(n, "GET"), True
                elif ft.startswith("axios.") and ft.split(".")[-1] in AXIOS_METHODS:
                    url_node, default_method, is_call = (args.named_children[0] if args.named_children else None), ft.split(".")[-1].upper(), True
            elif n.type == "new_expression":
                ctor = n.child_by_field_name("constructor")
                args = n.child_by_field_name("arguments")
                if ctor is not None and args is not None and ctor.text in (b"EventSource", b"WebSocket"):
                    url_node, default_method, is_call = (args.named_children[0] if args.named_children else None), "GET", True
            if not is_call or url_node is None:
                continue
            fn_node, fn_name = enclosing_function(n)
            raw = url_pattern(url_node, consts)
            # Helper detection: a parameter of the enclosing function flows into the URL.
            # A helper whose parameter is the whole tail (``${API_BASE}${path}``) describes no
            # endpoint itself; one with a parameter inside the path (``/worlds/${world}/seed``)
            # does, and is recorded as a direct call as well.
            tail_only = False
            if fn_node is not None and fn_name:
                params = param_names(fn_node)
                idents = substitution_idents(url_node) | ({url_node.text.decode()} if url_node.type == "identifier" else set())
                for i, pn in enumerate(params):
                    if pn in idents:
                        prefix = static_prefix(url_node, pn, consts) if url_node.type == "template_string" else ""
                        helpers[fn_name] = (i, prefix)
                        tail_only = url_node.type == "identifier" or (raw is not None and raw == prefix + "\x00")
                        break
            pat = normalize(raw) if raw else None
            if pat and pat != "/" and pat != "*" and not tail_only:
                calls.append(Call(rel, n.start_point[0] + 1, default_method, pat, "direct"))

    for rel, tree in trees.items():
        for n in walk(tree.root_node):
            if n.type != "call_expression":
                continue
            fn = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if fn is None or args is None:
                continue
            name = fn.text.decode().split(".")[-1]
            if name not in helpers:
                continue
            idx, prefix = helpers[name]
            if idx >= len(args.named_children):
                continue
            raw = url_pattern(args.named_children[idx], consts_by_file[rel])
            pat = normalize(prefix + raw) if raw else None
            if pat and pat != "/" and pat != "*":
                calls.append(Call(rel, n.start_point[0] + 1, method_of(n, "?"), pat, name))

    ctx.notes["http-helpers"] = helpers
    ctx.notes["http-calls"] = [c.__dict__ for c in calls]
    for c in calls:
        api.call(ctx, api.Call("http", c.method, c.pattern, c.file, c.line, c.via, "http-calls"))
