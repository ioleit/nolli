"""How the hover text talks about TypeScript and JavaScript: modules (one per file),
folders with an ``index`` file, classes, interfaces, type aliases and enums, and their
doc comments. Used by nolli/info.py; returns None for anything else."""

from __future__ import annotations

from ...prose import Ctx, count, listing, say
from ...rollup import is_init

NAMES = {"typescript": "TypeScript", "javascript": "JavaScript"}
SYMBOLS = {"class": "class", "interface": "interface", "type": "type alias", "enum": "enum"}


def lang_of(ctx: Ctx, x: str) -> str | None:
    return NAMES.get(ctx.m.entities[x].attrs.get("language") or "")


def index_of(ctx: Ctx, x: str) -> str | None:
    """A folder's ``index.ts`` (or .js, ...): the folder's own code."""
    m = ctx.m
    if m.entities[x].kind != "directory":
        return None
    return next((c for c in m.children(x) if is_init(m, c) and lang_of(ctx, c)), None)


def is_module(ctx: Ctx, x: str) -> bool:
    return ctx.m.entities[x].kind == "file" and lang_of(ctx, x) is not None


def is_symbol(ctx: Ctx, x: str) -> bool:
    return ctx.m.entities[x].kind in SYMBOLS and lang_of(ctx, x) is not None


def noun(ctx: Ctx, x: str) -> str | None:
    e = ctx.m.entities[x]
    if is_module(ctx, x):
        return "module" if e.attrs.get("role") in ("source", None) else lang_of(ctx, x) + " file"
    if is_symbol(ctx, x):
        return SYMBOLS[e.kind]
    return None


def kind(ctx: Ctx, x: str) -> str | None:
    """A folder with an index file is a module of its own, made of its files."""
    i = index_of(ctx, x)
    return f"a {lang_of(ctx, i)} folder module" if i else None


def describe(ctx: Ctx, x: str) -> list | None:
    e = ctx.m.entities[x]
    if is_module(ctx, x):
        lang = lang_of(ctx, x)
        facts = [count(e.attrs["lines"], "line")] if e.attrs.get("lines") is not None else []
        inner: dict[str, int] = {}
        for c in ctx.m.children(x):
            k = ctx.m.entities[c].kind
            inner[k] = inner.get(k, 0) + 1
        facts += [count(inner[k], SYMBOLS[k]) for k in SYMBOLS if inner.get(k)]
        if not facts:
            return [say("{} is a " + lang + " module", ctx.name(x))]
        return [say("{} is a " + lang + " module with ", ctx.name(x)) + listing("{}", facts)]
    if is_symbol(ctx, x):
        what = ("an exported " if e.attrs.get("exported") else "a ") + lang_of(ctx, x) + " " + SYMBOLS[e.kind]
        where = (e.attrs.get("path") or "").rsplit("/", 1)[-1]
        if where and e.attrs.get("line"):
            return [say("{} is " + what + " in {}, line {}", ctx.name(x), where, e.attrs["line"])]
        return [say("{} is " + what, ctx.name(x))]
    return None


def doc(ctx: Ctx, x: str) -> str | None:
    """The doc comment's summary: a folder module's from its index file."""
    i = index_of(ctx, x)
    if i or is_module(ctx, x) or is_symbol(ctx, x):
        return ctx.m.entities[i or x].attrs.get("doc")
    return None


def own_code(ctx: Ctx, owner: str) -> list | None:
    """The own-code dot of a module (what it holds outside its classes and types) or of a
    folder module (what its index file defines and re-exports)."""
    from ..python.describe import by_use

    i = index_of(ctx, owner)
    if not i and not is_module(ctx, owner):
        return None
    f = i or owner
    a = ctx.m.entities[f].attrs
    name = ctx.name(owner)
    index = ctx.m.entities[f].name
    lead = say("{}'s " + index + " defines ", name) if i else say("{} defines ", name)
    functions = by_use(ctx, f, a.get("functions") or [])
    constants = a.get("constants") or []
    lines: list[list] = []
    if functions:
        if len(functions) <= 3:
            lines.append(lead + listing("{}", functions))
        else:
            lines.append(lead + say("{}: ", count(len(functions), "function")) + listing("{}", functions, "more", limit=3))
    if constants:
        many = len(constants) > 3
        body = (say("{}: ", count(len(constants), "constant")) if many else say("the constant" + ("s " if len(constants) > 1 else " "))) \
            + listing("{}", constants, "more", limit=3)
        lines.append((["Also "] if functions else lead) + body)
    if a.get("reexports"):
        lines.append(say("It re-exports ") + listing("{}", a["reexports"], "more", limit=4) + say(" from its modules"))
    if not lines:
        lines.append(say("{}'s " + index + " only gathers the folder's modules", name) if i
                     else say("{} has no functions or constants outside its classes and types", name))
    total, own = a.get("lines"), a.get("own_lines")
    if total and own is not None and not i and own != total:
        lines.append(say("{} of its {} are outside its classes and types", f"{own:,}", count(total, "line")))
    elif total:
        lines.append(say("{}", count(total, "line")))
    return lines
