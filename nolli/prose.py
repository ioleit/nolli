"""Shared vocabulary for the hover sentences (info.py and each language's describe.py).

A description is Markdown, one paragraph per sentence, in a small subset the viewer
renders (viewer/js/md.js) and GitHub shows as it is:

- `code spans` for the data in a sentence ("`api-mock` references `1 name` in `lib/`"),
- a paragraph quoted with "> " for what the code says about itself (a docstring), its own
  code spans kept,
- ``[problem](verdict:problem)``, a link to the made-up scheme ``verdict:``, for a verdict's mark,
- backslash escapes; nothing else is interpreted.

Text from the repository goes in through ``code`` or ``esc``, never as it is. Ctx carries
what a description may look at: the model, the roll-up, the view's unfolded and hidden
sets, and how the picture names things.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import address
from .model import Model
from .rollup import RolledUp, chain_end, is_init
from .labels import label_of


def code(value) -> str:
    """A value as a code span, fenced with more backticks than it holds."""
    s = " ".join(str(value).split())
    if not s:
        return ""
    fence = "`" * (max((len(r) for r in re.findall(r"`+", s)), default=0) + 1)
    pad = " " if s[0] == "`" or s[-1] == "`" else ""
    return fence + pad + s + pad + fence


MD_PUNCT = re.compile(r"([\\`*_\[\]<>~|#])")


def esc(text: str) -> str:
    """Text from the repository as plain Markdown text, on one line."""
    return MD_PUNCT.sub(r"\\\1", " ".join(text.split()))


def quote(text: str) -> str:
    """What the code says about itself, as a quoted paragraph: its code spans (``x`` in a
    docstring, `x` in a doc comment) stay code spans, the rest is text."""
    text = " ".join(text.split())
    out, i = "", 0
    for run in re.finditer(r"`+", text):
        if run.start() < i:
            continue
        close = next((m for m in re.finditer(r"`+", text[run.end():]) if len(m.group()) == len(run.group())), None)
        if close is None:
            continue  # an unpaired run is text, escaped with the rest
        end = run.end() + close.end()
        out += MD_PUNCT.sub(r"\\\1", text[i:run.start()]) + text[run.start():end]
        i = end
    return "> " + out + MD_PUNCT.sub(r"\\\1", text[i:])


def mark(verdict: str) -> str:
    """A verdict's mark."""
    return f"[{verdict}](verdict:{verdict})"


def one_line(text: str) -> str:
    """Markdown from elsewhere (an agent's reason) as one paragraph."""
    return " ".join(text.split())


def paragraphs(lines: list[str]) -> str:
    return "\n\n".join(lines)


def say(template: str, *values) -> str:
    """A sentence: the template (Markdown), each {} filled with a value as a code span."""
    out = ""
    for i, text in enumerate(template.split("{}")):
        out += text + (code(values[i]) if i < len(values) else "")
    return out


def count(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or plural(one))}"


def plural(noun: str) -> str:
    return noun[:-1] + "ies" if noun.endswith("y") and noun[-2:-1] not in "aeiou" else noun + ("es" if noun.endswith(("s", "x")) else "s")


def listing(template: str, values: list[str], more_noun: str = "more", limit: int = 4) -> str:
    """'{}' filled with a list of values: "a", "a and b", "a, b and c", "a, b, c, d and 3 more"."""
    shown, rest = values[:limit], len(values) - limit
    items = shown + ([f"{rest} {more_noun}"] if rest > 0 else [])
    if len(items) == 1:
        return say(template, items[0])
    head, tail = template.split("{}", 1)
    return head + "".join((" and " if i == len(items) - 1 else ", " if i else "") + code(v) for i, v in enumerate(items)) + tail


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
        if address.is_own_code(x):
            return f"{self.name(address.owner(x))} (own code)"
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
