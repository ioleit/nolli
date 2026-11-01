"""HTML pages: the files of the repository a page loads, as ``use`` edges (see
``refs.py``): ``<script src>`` (via ``script``), ``<link href>`` (``stylesheet``,
``preload`` for module preloads, ``link`` for icons and the rest), and ``src`` of
``img``, ``source``, ``video``, ``audio``, ``iframe``, ``embed`` (``image``, ``media``,
``frame``). A page's ``<style>`` blocks are read as stylesheets (``css.py``). Links
between pages (``<a href>``) are navigation, not dependencies, and are not drawn.

Not read (yet): ``import`` statements inside inline ``<script type="module">``, and
``srcset``. Parsed with the standard library's tolerant ``html.parser``.
"""

from __future__ import annotations

from html.parser import HTMLParser

from ...model import address
from .. import Context
from . import css
from .refs import link

EXTENSIONS = (".html", ".htm")
SRC = {"script": "script", "img": "image", "source": "media", "video": "media", "audio": "media",
       "iframe": "frame", "embed": "frame"}


class Page(HTMLParser):
    def __init__(self, ctx: Context, rel: str) -> None:
        super().__init__(convert_charrefs=True)
        self.ctx, self.rel = ctx, rel
        self.style_line: int | None = None

    def ref(self, value: str | None, how: str) -> None:
        if value:
            link(self.ctx, self.rel, value, self.getpos()[0], how, "html")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in SRC:
            self.ref(a.get("src"), SRC[tag])
        elif tag == "link":
            rels = (a.get("rel") or "").lower().split()
            how = "stylesheet" if "stylesheet" in rels else "preload" if "modulepreload" in rels else "link"
            self.ref(a.get("href"), how)
        elif tag == "style":
            self.style_line = self.getpos()[0]

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.style_line = None

    def handle_data(self, data: str) -> None:
        if self.style_line is not None:
            css.scan(self.ctx, self.rel, data, self.getpos()[0], "html")


def run(ctx: Context) -> None:
    for f in ctx.progress.each(ctx.files):
        rel = ctx.rel(f)
        if rel.lower().endswith(EXTENSIONS) and ctx.model.has(address.repo(rel)):
            page = Page(ctx, rel)
            page.feed(ctx.read(rel).decode("utf-8", "replace"))
            page.close()
