"""Shared vocabulary for the hover sentences (info.py and each language's describe.py).

A sentence is a list of parts: plain strings, and {"v": value} for the data in it. Ctx
carries what a description may look at: the model, the roll-up, the view's unfolded and
hidden sets, and how the picture names things.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Model
from .rollup import RolledUp, chain_end, is_init
from .labels import is_body, label_of


def say(template: str, *values) -> list:
    """A sentence as parts: the template's text, and each {} filled with a value part."""
    parts: list = []
    for i, text in enumerate(template.split("{}")):
        if text:
            parts.append(text)
        if i < len(values):
            parts.append({"v": str(values[i])})
    return parts


def count(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or plural(one))}"


def plural(noun: str) -> str:
    return noun[:-1] + "ies" if noun.endswith("y") and noun[-2:-1] not in "aeiou" else noun + ("es" if noun.endswith(("s", "x")) else "s")


def listing(template: str, values: list[str], more_noun: str = "more", limit: int = 4) -> list:
    """'{}' filled with a list of values: "a", "a and b", "a, b and c", "a, b, c, d and 3 more"."""
    shown, rest = values[:limit], len(values) - limit
    items = shown + ([f"{rest} {more_noun}"] if rest > 0 else [])
    if len(items) == 1:
        return say(template, items[0])
    head, tail = template.split("{}", 1)
    parts: list = [head] if head else []
    for i, v in enumerate(items):
        if i:
            parts.append(" and " if i == len(items) - 1 else ", ")
        parts.append({"v": v})
    if tail:
        parts.append(tail)
    return parts


@dataclass
class Ctx:
    m: Model
    rolled: RolledUp
    expanded: set[str]
    hidden: set[str]
    utilities: set[str] = field(default_factory=set)

    def name(self, x: str) -> str:
        """A box as the picture names it: directories end in "/", a module's own code says so."""
        m = self.m
        if is_body(x):
            return f"{self.name(x[5:])} (own code)"
        e = m.entities[x]
        if e.kind in ("service", "image"):
            return e.name.removesuffix("/Dockerfile")  # no "svc " / "img " prefix in prose
        if e.kind == "endpoint":
            return f"{e.attrs.get('method', '')} {e.attrs.get('path', e.name)}".strip()
        label = label_of(m, x, self.expanded)
        return label + "/" if e.kind == "directory" and not label.endswith("/") else label

    def shown_children(self, x: str) -> list[str]:
        """What unfolding x draws: children not hidden, a package's __init__ replaced by its
        classes, single-child folder chains collapsed (rollup.visible_from_expanded)."""
        m, out = self.m, []
        kind = m.entities[x].kind
        for c in m.children(x):
            if c in self.hidden:
                continue
            if kind == "directory" and is_init(m, c):
                out.extend(cc for cc in m.children(c) if cc not in self.hidden)
                continue
            out.append(chain_end(m, c, self.hidden) if kind == "directory" and m.entities[c].kind == "directory" else c)
        return out

    def files_below(self, x: str) -> list[str]:
        """Files under x that the view shows (not hidden by their role)."""
        m, out, stack = self.m, [], [x]
        while stack:
            c = stack.pop()
            for k in m.children(c):
                if k in self.hidden:
                    continue
                if m.entities[k].kind == "file":
                    out.append(k)
                elif m.entities[k].kind == "directory":
                    stack.append(k)
        return out
