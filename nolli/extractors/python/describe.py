"""How the hover text talks about Python: packages (folders with __init__.py), modules,
classes, and their docstrings. Used by nolli/info.py; every function returns None for
anything that isn't Python's."""

from __future__ import annotations

from ...prose import Ctx, count, listing, say
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


def by_use(ctx: Ctx, file_id: str, names: list[str]) -> list[str]:
    """Names defined in a module, most referenced from elsewhere first (references carry
    the name they use), then in source order."""
    uses: dict[str, int] = {}
    wanted = set(names)
    for r in ctx.m.relations:
        if r.dst == file_id and r.subkind == "ref":
            n = r.attrs.get("name")
            if n in wanted:
                uses[n] = uses.get(n, 0) + r.count
    order = {n: i for i, n in enumerate(names)}
    return sorted(names, key=lambda n: (-uses.get(n, 0), order[n]))


def own_code(ctx: Ctx, owner: str) -> list | None:
    """The own-code dot of a module (what it holds outside its classes) or of a package
    (what its __init__.py defines and re-exports)."""
    init = init_of(ctx, owner)
    if not init and not is_module(ctx, owner):
        return None
    f = init or owner
    a = ctx.m.entities[f].attrs
    name = ctx.name(owner)
    lead = say("{}'s __init__.py defines ", name) if init else say("{} defines ", name)
    functions = by_use(ctx, f, a.get("functions") or [])
    constants = a.get("constants") or []
    lines: list[list] = []
    if functions:
        if len(functions) <= 3:  # "rollup defines bundle and rollup"
            lines.append(lead + listing("{}", functions))
        else:  # "… defines 20 functions: is_init, … and 17 more" (most used first)
            lines.append(lead + say("{}: ", count(len(functions), "function")) + listing("{}", functions, "more", limit=3))
    if constants:
        many = len(constants) > 3
        body = (say("{}: ", count(len(constants), "constant")) if many else say("the constant" + ("s " if len(constants) > 1 else " "))) \
            + listing("{}", constants, "more", limit=3)
        lines.append((["Also "] if functions else lead) + body)
    if init and a.get("reexports"):
        lines.append(say("It re-exports ") + listing("{}", a["reexports"], "more", limit=4) + say(" from its modules"))
    if not lines:
        lines.append(say("{}'s __init__.py only marks the folder as a package", name) if init
                     else say("{} has no functions or constants outside its classes", name))
    total, own = a.get("lines"), a.get("own_lines")
    if total and own is not None and not init and own != total:
        lines.append(say("{} of its {} are outside its classes", f"{own:,}", count(total, "line")))
    elif total:
        lines.append(say("{}", count(total, "line")))
    return lines
