"""How the hover text talks about TypeScript and JavaScript: modules (one per file).
Used by nolli/info.py; returns None for anything else."""

from __future__ import annotations

from ...prose import Ctx, count, say

NAMES = {"typescript": "TypeScript", "javascript": "JavaScript"}


def noun(ctx: Ctx, x: str) -> str | None:
    e = ctx.m.entities[x]
    if e.kind == "file" and e.attrs.get("language") in NAMES:
        return "module" if e.attrs.get("role") in ("source", None) else NAMES[e.attrs["language"]] + " file"
    return None


def kind(ctx: Ctx, x: str) -> str | None:
    return None


def own_code(ctx: Ctx, owner: str) -> list | None:
    return None


def doc(ctx: Ctx, x: str) -> str | None:
    return None


def describe(ctx: Ctx, x: str) -> list | None:
    e = ctx.m.entities[x]
    lang = NAMES.get(e.attrs.get("language") or "")
    if e.kind != "file" or not lang:
        return None
    if e.attrs.get("lines") is not None:
        return [say("{} is a " + lang + " module with {}", ctx.name(x), count(e.attrs["lines"], "line"))]
    return [say("{} is a " + lang + " module", ctx.name(x))]
