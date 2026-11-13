"""Finds the HTTP calls that TypeScript/JavaScript code makes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nolli.logic.extractors import Context, api
from nolli.logic.extractors.typescript import imports

AXIOS_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass
class Call:
    file: str
    line: int
    method: str
    pattern: str
    via: str  # "direct" or helper name


def _module_constants(root) -> dict[str, str]:
    """Top-level ``const NAME = "literal"`` (also ``x || "literal"``), for folding ``${API_BASE}``."""
    consts: dict[str, str] = {}

    def literal(n) -> str | None:
        if n is None:
            return None
        if n.type in ("string", "template_string"):
            v = _url_pattern(n, {})
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


MAX_SHAPES = 8  # shapes one URL expression may have; past that, the alternatives are not followed
MAX_DEPTH = 6  # names and calls followed for one URL expression


def _url_shapes(node, consts: dict[str, str] | None = None, depth: int = 0) -> list[str]:
    """Return the static shapes a URL expression can have, e.g. ``['/api/x/\x00']``; empty if nothing
    in it is static. A part that cannot be read becomes \x00. Module constants (``consts``) are
    folded in; an identifier is also read from the nearest enclosing ``const`` that declares it, a
    call to a function of the same file from the one expression it returns, and a conditional
    (``c ? "/a" : ""``) gives a shape for each branch."""
    consts = consts or {}
    if node is None or depth > MAX_DEPTH:
        return []
    t = node.type
    if t == "string":
        return [b"".join(c.text for c in node.children if c.type == "string_fragment").decode("utf-8", "replace")]
    if t == "identifier":
        name = node.text.decode()
        if name in consts:
            return [consts[name]]
        value = _find_const(node, name)
        return _url_shapes(value, consts, depth + 1) if value is not None else []
    if t == "template_string":
        parts: list[list[str]] = []
        for c in node.children:
            if c.type == "string_fragment":
                parts.append([c.text.decode("utf-8", "replace")])
            elif c.type == "template_substitution":
                inner = c.named_children[0] if c.named_children else None
                parts.append(_url_shapes(inner, consts, depth + 1) or ["\x00"])
        return [x for x in _combine(parts) if x.strip("\x00")]
    if t == "binary_expression":
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        return _combine([_url_shapes(left, consts, depth + 1) or ["\x00"], _url_shapes(right, consts, depth + 1) or ["\x00"]])
    if t == "parenthesized_expression" and node.named_children:
        return _url_shapes(node.named_children[0], consts, depth + 1)
    if t == "ternary_expression":
        branches = [_url_shapes(node.child_by_field_name(f), consts, depth + 1) or ["\x00"] for f in ("consequence", "alternative")]
        return list(dict.fromkeys(branches[0] + branches[1]))[:MAX_SHAPES]
    if t == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "identifier":
            returned = _find_returned(node, fn.text.decode())
            return _url_shapes(returned, consts, depth + 1) if returned is not None else []
    return []


def _url_pattern(node, consts: dict[str, str] | None = None) -> str | None:
    """Return the first static shape of a URL expression (``_url_shapes``), or None if nothing is static."""
    shapes = _url_shapes(node, consts)
    return shapes[0] if shapes else None


def _combine(parts: list[list[str]]) -> list[str]:
    """Concatenate one alternative of each part, every way, at most MAX_SHAPES of them."""
    out = [""]
    for alts in parts:
        out = list(dict.fromkeys(a + b for a in out for b in alts))[:MAX_SHAPES]
    return out


def _scopes(node):
    """Yield the blocks around ``node``, innermost first: function bodies, blocks, the module."""
    n = node.parent
    while n is not None:
        if n.type in ("statement_block", "program"):
            yield n
        n = n.parent


def _declarations(block):
    """Yield (name, value) of the ``const`` declarations directly in ``block``, exported ones too."""
    for n in block.named_children:
        decl = n.child_by_field_name("declaration") if n.type == "export_statement" else n
        if decl is None or decl.type != "lexical_declaration" or not decl.children or decl.children[0].type != "const":
            continue
        for d in decl.named_children:
            if d.type == "variable_declarator":
                name = d.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    yield name.text.decode(), d.child_by_field_name("value")


def _find_const(node, name: str):
    """Return the value of the nearest enclosing ``const <name> = …``, or None."""
    for block in _scopes(node):
        for n, value in _declarations(block):
            if n == name:
                return value
    return None


def _find_returned(node, name: str):
    """Return the one expression that the function ``name``, declared around ``node``, returns: an
    arrow function's expression body, or a body whose only ``return`` it is. None for anything else."""
    for block in _scopes(node):
        fns = [value for n, value in _declarations(block) if n == name]
        fns += [c for c in block.named_children if c.type == "function_declaration"
                and c.child_by_field_name("name") is not None and c.child_by_field_name("name").text.decode() == name]
        for fn in fns:
            if fn is None or fn.type not in ("arrow_function", "function_expression", "function", "function_declaration"):
                continue
            body = fn.child_by_field_name("body")
            if body is None:
                return None
            if body.type != "statement_block":
                return body
            returns = [n for n in imports.walk(body) if n.type == "return_statement"]
            return returns[0].named_children[0] if len(returns) == 1 and returns[0].named_children else None
    return None


def _static_prefix(node, param: str, consts: dict[str, str]) -> str:
    """Return the static text of a URL template before the substitution that carries ``param``."""
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


def _normalize(p: str) -> str | None:
    p = p.split("?")[0]
    p = re.sub(r"^\x00+", "", p)  # leading ${BASE}
    p = re.sub(r"^https?://[^/]*", "", p)
    if not p.startswith("/"):
        return None
    p = re.sub(r"\x00+", "*", p)
    p = re.sub(r"/\*[^/]*", "/*", p)  # '/x/*suffix' -> '/x/*'
    p = re.sub(r"[^/]*\*", "*", p)
    return p.rstrip("/") or "/"


def _method_of(call_node, default: str) -> str:
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


def _enclosing_function(node):
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


def _param_names(fn_node) -> list[str]:
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


def _substitution_idents(node) -> set[str]:
    out = set()
    for n in imports.walk(node):
        if n.type == "template_substitution":
            for c in imports.walk(n):
                if c.type == "identifier":
                    out.add(c.text.decode())
        if n.type == "identifier" and node.type == "binary_expression":
            out.add(n.text.decode())
    return out


def run(ctx: Context) -> None:
    """Report every HTTP call whose URL can be read from the code.

    Two passes over the syntax trees:

    1. Direct calls: ``fetch(<url>)``, ``axios.<method>(<url>)``,
       ``new EventSource(<url>)``, ``new WebSocket(<url>)``. The URL's static
       shapes are kept (``_url_shapes``: template substitutions become ``*`` segments,
       a leading substitution such as ``${API_BASE}`` is dropped, local constants and
       same-file functions returning a URL are read, a conditional gives a call per branch).
    2. One level of indirection: a function whose body passes one of its
       parameters into such a URL is a *helper*; every call to a function of that
       name elsewhere contributes its argument as a URL.

    Calls go to the ``api`` module as ``unresolved:http:<METHOD or ?> <pattern>``
    placeholders, to be bound to endpoints later."""
    trees: dict[str, object] = {}
    for rel in ctx.sources("typescript", tests=False):
        tree = ctx.tree(rel)
        if tree is not None:
            trees[rel] = tree

    calls: list[Call] = []
    helpers: dict[str, tuple[int, str]] = {}  # helper name -> (parameter index carrying the URL, static prefix)
    consts_by_file = {rel: _module_constants(tree.root_node) for rel, tree in trees.items()}

    for rel, tree in trees.items():
        consts = consts_by_file[rel]
        for n in imports.walk(tree.root_node):
            url_node, default_method, is_call = None, "?", False
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                if fn is None or args is None:
                    continue
                ft = fn.text.decode()
                if ft == "fetch" or ft.endswith(".fetch"):
                    url_node, default_method, is_call = (args.named_children[0] if args.named_children else None), _method_of(n, "GET"), True
                elif ft.startswith("axios.") and ft.split(".")[-1] in AXIOS_METHODS:
                    url_node, default_method, is_call = (args.named_children[0] if args.named_children else None), ft.split(".")[-1].upper(), True
            elif n.type == "new_expression":
                ctor = n.child_by_field_name("constructor")
                args = n.child_by_field_name("arguments")
                if ctor is not None and args is not None and ctor.text in (b"EventSource", b"WebSocket"):
                    url_node, default_method, is_call = (args.named_children[0] if args.named_children else None), "GET", True
            if not is_call or url_node is None:
                continue
            fn_node, fn_name = _enclosing_function(n)
            shapes = _url_shapes(url_node, consts)
            raw = shapes[0] if shapes else None
            # Helper detection: a parameter of the enclosing function flows into the URL.
            # A helper whose parameter is the whole tail (``${API_BASE}${path}``) describes no
            # endpoint itself; one with a parameter inside the path (``/worlds/${world}/seed``)
            # does, and is recorded as a direct call as well.
            tail_only = False
            if fn_node is not None and fn_name:
                params = _param_names(fn_node)
                idents = _substitution_idents(url_node) | ({url_node.text.decode()} if url_node.type == "identifier" else set())
                for i, pn in enumerate(params):
                    if pn in idents:
                        prefix = _static_prefix(url_node, pn, consts) if url_node.type == "template_string" else ""
                        helpers[fn_name] = (i, prefix)
                        tail_only = url_node.type == "identifier" or (raw is not None and raw == prefix + "\x00")
                        break
            if not tail_only:
                for pat in dict.fromkeys(_normalize(x) for x in shapes):
                    if pat and pat != "/" and pat != "*":
                        calls.append(Call(rel, n.start_point[0] + 1, default_method, pat, "direct"))

    for rel, tree in trees.items():
        for n in imports.walk(tree.root_node):
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
            for pat in dict.fromkeys(_normalize(prefix + x) for x in _url_shapes(args.named_children[idx], consts_by_file[rel])):
                if pat and pat != "/" and pat != "*":
                    calls.append(Call(rel, n.start_point[0] + 1, _method_of(n, "?"), pat, name))

    ctx.notes["http-helpers"] = helpers
    ctx.notes["http-calls"] = [c.__dict__ for c in calls]
    for c in calls:
        api.call(ctx, api.Call("http", c.method, c.pattern, c.file, c.line, c.via, "http-calls"))
