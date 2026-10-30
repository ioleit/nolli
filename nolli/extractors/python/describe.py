"""How the hover text talks about Python: packages (folders with __init__.py), modules,
classes, and their docstrings. Used by nolli/info.py; every function returns None for
anything that isn't Python's."""

from __future__ import annotations

from ...prose import Ctx, count, say
from ...rollup import is_init


def init_of(ctx: Ctx, x: str) -> str | None:
    m = ctx.m
    if m.entities[x].kind != "directory":
        return None
    return next((c for c in m.children(x) if is_init(m, c)), None)


def is_module(ctx: Ctx, x: str) -> bool:
    e = ctx.m.entities[x]
    return e.kind == "file" and e.attrs.get("language") == "python"


def noun(ctx: Ctx, x: str) -> str | None:
    """What a box counts as inside its folder."""
    e = ctx.m.entities[x]
    if init_of(ctx, x):
        return "subpackage"
    if is_module(ctx, x):
        return "module" if e.attrs.get("role") in ("source", None) else "Python file"
    if e.kind == "class" and e.attrs.get("language") == "python":
        return "class"
    return None


def kind(ctx: Ctx, x: str) -> str | None:
    """What a folder is, for "<name> is <kind> with …"."""
    return "a Python package" if init_of(ctx, x) else None


def describe(ctx: Ctx, x: str) -> list | None:
    """The first sentence for a module or class (folders: see kind)."""
    e = ctx.m.entities[x]
    if is_module(ctx, x):
        facts = [count(e.attrs["lines"], "line")] if e.attrs.get("lines") is not None else []
        classes = ctx.m.children(x)
        if classes:
            facts.append(count(len(classes), "class", "classes"))
        if not facts:
            return [say("{} is a Python module", ctx.name(x))]
        return [say("{} is a Python module with " + " and ".join(["{}"] * len(facts)), ctx.name(x), *facts)]
    if e.kind == "class" and e.attrs.get("language") == "python":
        where = (e.attrs.get("path") or "").rsplit("/", 1)[-1]
        if where and e.attrs.get("line"):
            return [say("{} is a Python class in {}, line {}", ctx.name(x), where, e.attrs["line"])]
        return [say("{} is a Python class", ctx.name(x))]
    return None


def doc(ctx: Ctx, x: str) -> str | None:
    """The docstring summary: a package's from its __init__.py."""
    init = init_of(ctx, x)
    e = ctx.m.entities[init or x]
    if init or is_module(ctx, x) or (e.kind == "class" and e.attrs.get("language") == "python"):
        return e.attrs.get("doc")
    return None
