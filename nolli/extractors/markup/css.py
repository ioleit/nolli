"""Stylesheets: ``@import`` and ``url(...)`` references to files of the repository
(other stylesheets, fonts, images), as ``use`` edges (see ``refs.py``). Read with
regular expressions after comments are blanked out; no CSS parser is needed for these
two forms."""

from __future__ import annotations

import re

from .. import Context
from .refs import link

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?(['"]?)([^'")\s;]+)\1""", re.IGNORECASE)
URL = re.compile(r"""url\(\s*(['"]?)([^'")]+?)\1\s*\)""", re.IGNORECASE)
EXTENSIONS = (".css", ".scss", ".less")


def scan(ctx: Context, rel: str, text: str, first_line: int = 1, extractor: str = "css") -> None:
    """References in CSS ``text`` that starts at ``first_line`` of ``rel`` (a stylesheet,
    or a page's ``<style>``)."""
    text = COMMENT.sub(lambda mt: re.sub(r"[^\n]", " ", mt.group()), text)
    imports = set()
    for mt in IMPORT.finditer(text):
        imports.add(mt.start(2))
        link(ctx, rel, mt.group(2), first_line + text.count("\n", 0, mt.start()), "import", extractor)
    for mt in URL.finditer(text):
        if mt.start(2) not in imports:
            link(ctx, rel, mt.group(2), first_line + text.count("\n", 0, mt.start()), "url", extractor)


def run(ctx: Context) -> None:
    for f in ctx.progress.each(ctx.files):
        rel = ctx.rel(f)
        if rel.lower().endswith(EXTENSIONS) and ctx.model.has(ctx.fs_id(rel)):
            scan(ctx, rel, ctx.read(rel).decode("utf-8", "replace"))
