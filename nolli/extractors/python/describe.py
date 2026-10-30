"""How the hover text talks about Python: packages (folders with __init__.py), modules,
classes. Used by nolli/info.py; returns None for anything that isn't Python's."""

from __future__ import annotations

from ...prose import Ctx, count, say
from ...rollup import is_init


def is_package(ctx: Ctx, x: str) -> bool:
    m = ctx.m
    return m.entities[x].kind == "directory" and any(is_init(m, c) for c in m.children(x))


def is_module(ctx: Ctx, x: str) -> bool:
    e = ctx.m.entities[x]
    return e.kind == "file" and e.attrs.get("language") == "python"


def noun(ctx: Ctx, x: str) -> str | None:
    """What a box counts as when its container unfolds."""
    e = ctx.m.entities[x]
    if is_package(ctx, x):
        return "package"
    if is_module(ctx, x):
        return "module" if e.attrs.get("role") in ("source", None) else "Python file"
    if e.kind == "class" and e.attrs.get("language") == "python":
        return "class"
    return None


def describe(ctx: Ctx, x: str) -> list | None:
    e = ctx.m.entities[x]
    if is_package(ctx, x):
        return [say("{} is a Python package", ctx.name(x))]
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
