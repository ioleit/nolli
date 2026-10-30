"""What the viewer shows when the pointer rests on a box or an edge: plain sentences.

A description is a list of lines; a line is a list of parts: plain strings, {"v": value}
for the data in it ("api-mock references 1 name in lib/"), and {"doc": text} for what the
code says about itself (a docstring). The viewer sets both apart.

What a box *is* comes from its language's describe module next to its extractors
(extractors/python/describe.py, …); this module adds what is the same for every
language: what unfolding a folder shows, its size, and what a box depends on outside the
picture (external packages, utilities, environment, deployment, HTTP), which the boxes
themselves no longer carry as badges.
"""

from __future__ import annotations

from collections import defaultdict

from .extractors import describers
from .labels import is_body
from .model import ALLOCATE, PROVIDE, USE
from .prose import Ctx, count, listing, say

GENERIC_NOUN = {"directory": "subfolder", "file": "file", "class": "class", "endpoint": "endpoint", "group": "group",
                "env": "variable", "service": "service", "image": "image", "package": "package", "host": "host"}
WHAT = {"endpoint": "an HTTP endpoint", "env": "an environment variable", "service": "a Compose service",
        "image": "a Docker image", "package": "an external package", "host": "a host"}
NOUN_ORDER = ["subpackage", "subfolder", "module", "class"]  # earlier in a list; the rest by count


def noun(ctx: Ctx, x: str) -> str:
    return first("noun", ctx, x) or GENERIC_NOUN.get(ctx.m.entities[x].kind, ctx.m.entities[x].kind)


def first(fn: str, ctx: Ctx, x: str):
    """The first language describer's answer to fn (noun, kind, describe, doc)."""
    for d in describers():
        said = getattr(d, fn)(ctx, x)
        if said:
            return said
    return None


def folder(ctx: Ctx, x: str) -> list:
    """"agents/ is a Python package with 6 subpackages and 3 modules": what it holds,
    counted as unfolding draws it (hidden roles left out, __init__ classes in)."""
    e = ctx.m.entities[x]
    what = "the repository" if e.attrs.get("root") else first("kind", ctx, x) or "a folder"
    by: dict[str, int] = defaultdict(int)
    for c in ctx.shown_children(x):
        by[noun(ctx, c)] += 1
    if not by:
        return say("{} is " + what, ctx.name(x))
    order = sorted(by, key=lambda k: (NOUN_ORDER.index(k) if k in NOUN_ORDER else len(NOUN_ORDER), -by[k], k))
    return say("{} is " + what + " with ", ctx.name(x)) + listing("{}", [count(by[k], k) for k in order], limit=6)


def size(ctx: Ctx, x: str) -> list | None:
    files = ctx.files_below(x)
    if not files:
        return None
    lines = sum(ctx.m.entities[f].attrs.get("lines") or 0 for f in files)
    if lines:
        return say("{} with {} in all", count(len(files), "file"), count(lines, "line"))
    return say("{} in all", count(len(files), "file"))


class Dependencies:
    """What each drawn box uses outside the picture, from the facts (not the badges): the
    external packages, environment variables, endpoints and deployment its code touches,
    collected once per scene for every drawn box."""

    def __init__(self, ctx: Ctx, drawn: set[str]):
        m = ctx.m
        self.ext: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.env: dict[str, set[str]] = defaultdict(set)
        self.serves: dict[str, set[str]] = defaultdict(set)
        self.calls: dict[str, set[str]] = defaultdict(set)
        self.deployed: dict[str, set[str]] = defaultdict(set)

        def boxes(code: str):
            """The drawn boxes holding code (itself included), unless it is hidden."""
            chain, cur = [], code
            while cur is not None:
                if cur in ctx.hidden:
                    return []
                if cur in drawn:
                    chain.append(cur)
                cur = m.parent(cur)
            return chain

        for r in m.relations:
            if r.kind not in (USE, PROVIDE, ALLOCATE) or not m.has(r.dst) or not m.has(r.src):
                continue
            d, s = m.entities[r.dst], m.entities[r.src]
            if d.kind == "package" and r.kind == USE:
                for b in boxes(r.src):
                    self.ext[b][d.name] += r.count
            elif d.kind == "env" and r.kind == USE:
                for b in boxes(r.src):
                    self.env[b].add(d.name)
            elif d.kind == "endpoint":
                target = self.serves if r.kind == PROVIDE else self.calls if r.kind == USE else None
                if target is not None:
                    for b in boxes(r.src):
                        target[b].add(d.id)
            elif d.kind in ("service", "image") and r.kind == ALLOCATE:
                for b in boxes(r.src):
                    self.deployed[b].add(d.name.removesuffix("/Dockerfile"))
            elif s.kind in ("service", "image") and r.subkind == "entrypoint":
                for b in boxes(r.dst):
                    self.deployed[b].add(s.name.removesuffix("/Dockerfile"))

    def lines(self, ctx: Ctx, x: str) -> list[list]:
        out = []
        ext = self.ext.get(x)
        if ext:
            names = sorted(ext, key=lambda k: (-ext[k], k))
            out.append(say("Uses {}: ", count(len(names), "external package")) + listing("{}", names, "more"))
        utils = sorted({g[5:] for g in (ctx.rolled.annotations.get(x) or {}) if g.startswith("util:")})
        if utils:
            out.append(listing("Uses the utilit" + ("y" if len(utils) == 1 else "ies") + " {}", [ctx.name(u) for u in utils]))
        if self.env.get(x):
            out.append(say("Reads {}", count(len(self.env[x]), "environment variable")))
        if self.serves.get(x):
            out.append(say("Serves {}", count(len(self.serves[x]), "HTTP endpoint")))
        if self.calls.get(x):
            out.append(say("Calls {}", count(len(self.calls[x]), "HTTP endpoint")))
        if self.deployed.get(x):
            out.append(listing("Deployed in {}", sorted(self.deployed[x])))
        return out


def node_info(ctx: Ctx, deps: Dependencies, x: str, used_by: int = 0) -> list:
    m = ctx.m
    if is_body(x):
        owner = ctx.name(x[5:])
        if m.entities[x[5:]].kind == "directory":
            return [say("The code in {}'s __init__.py beyond re-exporting its modules", owner)]
        return [say("The code in {} outside any class", owner)]
    e = m.entities[x]
    lines: list[list] = []
    said = None if e.kind == "directory" else first("describe", ctx, x)
    if said:
        lines += said
    else:
        if e.kind == "directory":
            lines.append(folder(ctx, x))
        elif e.kind == "file":
            lines.append(say("{} is a file with {}", ctx.name(x), count(e.attrs["lines"], "line"))
                         if e.attrs.get("lines") is not None else say("{} is a file", ctx.name(x)))
        elif e.kind == "group":
            lines.append(say("{} holds {}", ctx.name(x), count(len(m.children(x)), "entry", "entries")))
        else:
            lines.append(say("{} is " + WHAT.get(e.kind, "a " + e.kind), ctx.name(x)))
    docstring = first("doc", ctx, x)
    if docstring:
        lines.append([{"doc": docstring}])
    if e.kind == "directory":
        total = size(ctx, x)
        if total:
            lines.append(total)
    lines += deps.lines(ctx, x)
    if used_by:
        lines.append(say("A utility, used by {}: their arrows into it are left out", count(used_by, "box", "boxes")))
    return lines


# per edge subkind: a sentence with {} for source, count and target
COUNTED = {
    "import": ("{} references {} in {}", "name"),
    "http": ("{} makes {} to endpoints served by {}", "call"),
    "endpoint": ("{} makes {} to endpoints served by {}", "call"),
    "env": ("{} reads {} set by {}", "variable"),
}
# deployment relations: a sentence with {} for source and target
PLAIN = {
    "depends_on": "{} depends on {}",
    "url": "{} has a URL pointing at {}",
    "copies": "{} is copied into {}",
    "entrypoint": "{} starts {}",
    "runs_as": "{} runs as {}",
}


def edge_info(ctx: Ctx, e) -> list:
    src, dst = ctx.name(e.src), ctx.name(e.dst)
    sub = e.subkind.removesuffix("-dangling")
    dangling = e.subkind.endswith("-dangling")
    if e.kind == "provide":
        return [say("{} serves endpoints that nothing here calls", dst)] if dangling else [say("{} is served by {}", src, dst)]
    if dangling:
        return [say("{} reads {} that nothing here sets", src, count(e.count, "variable"))] if sub == "env" \
            else [say("{} uses {} that nothing here provides", src, count(e.count, "interface"))]
    if sub in COUNTED:
        template, noun_ = COUNTED[sub]
        return [say(template, src, count(e.count, noun_), dst)]
    return [say(PLAIN.get(sub, "{} uses {}"), src, dst)]
