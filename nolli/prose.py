"""Shared vocabulary for the hover sentences (info.py and each language's describe.py).

A description is Markdown, one paragraph per sentence, in a small subset the viewer
renders (viewer/js/md.js) and GitHub shows as it is:

- `code spans` for the data in a sentence ("`api-mock` references `1 name` in `lib/`"),
- a paragraph quoted with "> " for what the code says about itself (a docstring), its own
  code spans kept,
- ``[problem](verdict:problem)``, a link to the made-up scheme ``verdict:``, for a verdict's mark,
- backslash escapes; nothing else is interpreted.

Text from the repository goes in through ``code`` or ``esc``, never as it is.
"""

from __future__ import annotations

import re


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
