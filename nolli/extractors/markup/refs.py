"""Shared by the HTML and CSS extractors: a reference written in a page or stylesheet
(``js/main.js``, ``../fonts/a.woff2``, ``/src/main.tsx``) to the file it names.

A relative reference is resolved against the referring file's directory. A reference
from the server root (``/src/main.tsx``) names a path the server decides; it is tried
against the referring file's directory and each directory above it up to the repository
root, also in a ``public/`` folder there; the nearest that has the file wins (Vite
serves ``/src/...`` from the directory of ``index.html`` and ``/logo.png`` from
``public/``), and is marked heuristic.

A script from a package CDN (``https://cdn.jsdelivr.net/npm/elkjs@0.9.3/...``) and a
stylesheet ``@import`` of a bare package name (``@import "tailwindcss"``) are uses of
that npm package, the same ``ext:js:`` entity the TypeScript front-end gives it
(``package`` below). Other URLs with a scheme (``https:``, ``data:``), protocol-relative
ones (``//cdn...``), fragments and template expressions (``{{ x }}``, ``${x}``) are not
the repository's files and are skipped. A reference that looks like a file of the
repository but names none is noted under ``markup-unmatched``.
"""

from __future__ import annotations

import posixpath
import re

from ...model import USE, Provenance, Relation
from .. import Context
from ..interfaces import ensure_ext
from ..typescript.resolve import CDN, package_name

SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
TEMPLATE = re.compile(r"\{\{|\{%|\$\{|<%")
BARE = re.compile(r"^(@[\w.-]+/)?[a-zA-Z][\w.-]*(/[\w./-]*)?$")


def package(ref: str, how: str) -> tuple[str, str] | None:
    """(npm package, confidence) a reference uses: a script from a package CDN, or a
    stylesheet ``@import`` of a bare name that is no file name (``tailwindcss``, not
    ``base.css``); heuristic, since plain CSS would read that as a relative path, which
    bundlers (PostCSS, Vite, Tailwind) do not."""
    if how == "script" and CDN.match(ref):
        return package_name(ref), "exact"
    if how == "import" and BARE.match(ref) and not ref.lower().endswith((".css", ".scss", ".less")):
        return package_name(ref), "heuristic"
    return None


def target(ctx: Context, rel: str, ref: str) -> tuple[str, str] | None:
    """(file id, confidence) of the repository file ``ref`` in ``rel`` names, else None."""
    ref = ref.strip()
    if not ref or ref.startswith(("#", "//")) or SCHEME.match(ref) or TEMPLATE.search(ref):
        return None
    path = re.split(r"[?#]", ref, maxsplit=1)[0]
    if not path or path.endswith("/"):
        return None
    here = posixpath.dirname(rel)
    if path.startswith("/"):
        d = here
        while True:
            fid = ctx.fs_id(posixpath.normpath(posixpath.join(d, path.lstrip("/"))))
            if file_exists(ctx, fid):
                return fid, "heuristic"
            fid = ctx.fs_id(posixpath.normpath(posixpath.join(d, "public", path.lstrip("/"))))
            if file_exists(ctx, fid):
                return fid, "heuristic"
            if not d:
                break
            d = posixpath.dirname(d)
    else:
        p = posixpath.normpath(posixpath.join(here, path))
        if not p.startswith("../") and file_exists(ctx, ctx.fs_id(p)):
            return ctx.fs_id(p), "exact"
    ctx.notes.setdefault("markup-unmatched", []).append((rel, ref))
    return None


def file_exists(ctx: Context, fid: str) -> bool:
    e = ctx.model.entities.get(fid)
    return e is not None and e.kind == "file"


def link(ctx: Context, rel: str, ref: str, line: int, how: str, extractor: str) -> None:
    """A ``use`` edge from the file ``rel`` to the file ``ref`` names, if it names one;
    or to the npm package it uses (``package``); ``how`` (``script``, ``stylesheet``,
    ``image``, ``import``, ``url``) is kept in attrs."""
    pkg = package(ref.strip(), how)
    t = (ensure_ext(ctx.model, "js", pkg[0], extractor), pkg[1]) if pkg else target(ctx, rel, ref)
    if t is None or t[0] == ctx.fs_id(rel):
        return
    fid, confidence = t
    language = ctx.model.entities[ctx.fs_id(rel)].attrs.get("language")
    ctx.model.add_relation(Relation(USE, ctx.fs_id(rel), fid, "import", 1, confidence, Provenance(extractor, rel, line),
                                    {"language": language, "via": how}))
