"""Writes descriptions as Markdown sentences.

Descriptions use a small subset of Markdown that reads well as plain text: one paragraph per
sentence, code spans, quoted paragraphs, ``verdict:`` links and backslash escapes; nothing else
is interpreted. Text from the repository goes in only escaped or as code, never as it is.
"""

from __future__ import annotations

import re


def code(value) -> str:
    """Format a value as a code span, fenced with more backticks than it holds; an empty value gives ""."""
    s = " ".join(str(value).split())
    if not s:
        return ""
    fence = "`" * (max((len(r) for r in re.findall(r"`+", s)), default=0) + 1)
    pad = " " if s[0] == "`" or s[-1] == "`" else ""
    return fence + pad + s + pad + fence


MD_PUNCT = re.compile(r"([\\`*_\[\]<>~|#])")


def _esc(text: str) -> str:
    """Text from the repository as plain Markdown text, on one line."""
    return MD_PUNCT.sub(r"\\\1", " ".join(text.split()))


def quote(text: str) -> str:
    """Quote what the code says about itself (a docstring) as a paragraph starting with "> ". Its
    code spans (``x`` in a docstring, `x` in a doc comment) stay code spans, and the rest is
    escaped as text."""
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
    """Return a verdict's mark, a link to the made-up scheme ``verdict:`` such as
    ``[problem](verdict:problem)``."""
    return f"[{verdict}](verdict:{verdict})"


def one_line(text: str) -> str:
    """Markdown from elsewhere (an agent's reason) as one paragraph."""
    return " ".join(text.split())


def paragraphs(lines: list[str]) -> str:
    """Sentences as Markdown, one paragraph each."""
    return "\n\n".join(lines)


def say(template: str, *values) -> str:
    """Write a sentence from the template (Markdown), filling each {} with a value as a code span
    so that the data in it stands out ("`api-mock` references `1 name` in `lib/`")."""
    out = ""
    for i, text in enumerate(template.split("{}")):
        out += text + (code(values[i]) if i < len(values) else "")
    return out


def count(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or _plural(one))}"


def _plural(noun: str) -> str:
    return noun[:-1] + "ies" if noun.endswith("y") and noun[-2:-1] not in "aeiou" else noun + ("es" if noun.endswith(("s", "x")) else "s")


def listing(template: str, values: list[str], more_noun: str = "more", limit: int = 4) -> str:
    """'{}' filled with a list of values: "a", "a and b", "a, b and c", "a, b, c, d and 3 more"."""
    shown, rest = values[:limit], len(values) - limit
    items = shown + ([f"{rest} {more_noun}"] if rest > 0 else [])
    if len(items) == 1:
        return say(template, items[0])
    head, tail = template.split("{}", 1)
    return head + "".join((" and " if i == len(items) - 1 else ", " if i else "") + code(v) for i, v in enumerate(items)) + tail
