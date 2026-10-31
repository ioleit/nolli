"""HTML pages and stylesheets on a synthetic repository: what a page loads (scripts,
stylesheets, images) and what a stylesheet imports, as edges between files or to npm
packages, and what is not a dependency (links, other sites, templates)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.build import extract
from nolli.rollup import hidden_by_role

FILES = {
    "site/index.html": (
        "<!doctype html>\n<html><head>\n"
        '<link rel="stylesheet" href="css/site.css">\n'
        '<link rel="icon" href="/favicon.ico">\n'
        '<link rel="modulepreload" href="./js/app.js?v=2">\n'
        '<script src="https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js"></script>\n'
        '<script src="https://example.com/analytics.js"></script>\n'
        "<style>\n  body { background: url('img/bg.png') }\n</style>\n"
        "</head><body>\n"
        '<img src="img/logo.svg" alt="">\n'
        '<img src="img/{{ name }}.png">\n'
        '<a href="about.html">About</a>\n'
        '<img src="img/missing.png">\n'
        '<script type="module" src="js/app.js"></script>\n'
        "</body></html>\n"
    ),
    "site/about.html": "<p>About</p>\n",
    "site/js/app.js": "export const x = 1\n",
    "site/css/site.css": (
        "/* @import 'commented.css'; */\n"
        '@import "base.css";\n'
        "@import url(theme.css) screen;\n"
        '@import "tailwindcss";\n'
        "@font-face { src: url(../fonts/a.woff2) format('woff2'); }\n"
        ".x { background: url(data:image/png;base64,AAAA) }\n"
    ),
    "site/css/base.css": "body { margin: 0 }\n",
    "site/css/theme.css": "body { color: red }\n",
    "site/fonts/a.woff2": "",
    "site/img/bg.png": "",
    "site/img/logo.svg": "<svg/>",
    "site/public/favicon.ico": "",
    "app/index.html": '<div id="root"></div>\n<script type="module" src="/src/main.ts"></script>\n',
    "app/src/main.ts": "export {}\n",
}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("markup")
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return extract(root)


def loads(m) -> dict[tuple[str, str], tuple[str, str]]:
    return {(r.src, r.dst): (r.attrs["via"], r.confidence) for r in m.relations
            if r.kind == "use" and r.provenance and r.provenance.extractor in ("html", "css")}


def test_what_a_page_loads(model):
    m, _ = model
    page = {dst: how for (src, dst), how in loads(m).items() if src == "fs:site/index.html"}
    assert page == {
        "fs:site/css/site.css": ("stylesheet", "exact"),
        "fs:site/public/favicon.ico": ("link", "heuristic"),  # from the server root: public/
        "fs:site/js/app.js": ("script", "exact"),
        "ext:js:elkjs": ("script", "exact"),
        "fs:site/img/bg.png": ("url", "exact"),  # from the page's <style>
        "fs:site/img/logo.svg": ("image", "exact"),
    }, "links between pages, other sites and template expressions are no dependencies"
    js = [r for r in m.relations if (r.src, r.dst) == ("fs:site/index.html", "fs:site/js/app.js")]
    assert sorted(r.attrs["via"] for r in js) == ["preload", "script"]
    assert [r.provenance.line for r in m.relations if r.kind == "use" and r.dst == "fs:site/img/bg.png"] == [9]


def test_what_a_stylesheet_loads(model):
    m, ctx = model
    sheet = {dst: how for (src, dst), how in loads(m).items() if src == "fs:site/css/site.css"}
    assert sheet == {
        "fs:site/css/base.css": ("import", "exact"),
        "fs:site/css/theme.css": ("import", "exact"),
        "ext:js:tailwindcss": ("import", "heuristic"),
        "fs:site/fonts/a.woff2": ("url", "exact"),
    }, "comments and data: URLs are skipped"
    assert ("site/index.html", "img/missing.png") in ctx.notes["markup-unmatched"]


def test_a_root_path_from_the_pages_directory(model):
    m, _ = model
    assert loads(m)[("fs:app/index.html", "fs:app/src/main.ts")] == ("script", "heuristic")


def test_pages_are_shown_by_default(model):
    m, _ = model
    assert "fs:site/index.html" not in hidden_by_role(m), "a page's edges would be dropped with it"
