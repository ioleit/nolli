"""HTML pages and stylesheets on a synthetic repository: what a page loads (scripts,
stylesheets, images) and what a stylesheet imports, as edges between files or to npm
packages, and what is not a dependency (links, other sites, templates)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.logic.extractors.facts import extract_facts
from nolli.logic.sources import git
from nolli.logic.transformations.projection.reading import Reading as Graph
from nolli.logic.transformations.projection.rollup import hidden_by_role
from nolli.model.keys import StringKey
from nolli.scenes import open_built

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
    return extract_facts(root, git.list_tracked_files(root))


def loads(m) -> dict[tuple[str, str], tuple[str, str]]:
    return {(r.src, r.dst): (r.attrs["via"], r.confidence) for r in m.relations
            if r.kind == "use" and r.provenance and r.provenance.extractor in ("html", "css")}


def test_what_a_page_loads(model):
    m = model
    page = {dst: how for (src, dst), how in loads(m).items() if src == "repo:site/index.html"}
    assert page == {
        "repo:site/css/site.css": ("stylesheet", "exact"),
        "repo:site/public/favicon.ico": ("link", "heuristic"),  # from the server root: public/
        "repo:site/js/app.js": ("script", "exact"),
        "pkg:npm/elkjs": ("script", "exact"),
        "repo:site/img/bg.png": ("url", "exact"),  # from the page's <style>
        "repo:site/img/logo.svg": ("image", "exact"),
    }, "links between pages, other sites and template expressions are no dependencies"
    js = [r for r in m.relations if (r.src, r.dst) == ("repo:site/index.html", "repo:site/js/app.js")]
    assert sorted(r.attrs["via"] for r in js) == ["preload", "script"]
    assert [r.provenance.line for r in m.relations if r.kind == "use" and r.dst == "repo:site/img/bg.png"] == [9]


def test_what_a_stylesheet_loads(model):
    m = model
    sheet = {dst: how for (src, dst), how in loads(m).items() if src == "repo:site/css/site.css"}
    assert sheet == {
        "repo:site/css/base.css": ("import", "exact"),
        "repo:site/css/theme.css": ("import", "exact"),
        "pkg:npm/tailwindcss": ("import", "heuristic"),
        "repo:site/fonts/a.woff2": ("url", "exact"),
    }, "comments and data: URLs are skipped"
    assert ("site/index.html", "img/missing.png") in m.meta["notes"]["markup-unmatched"]


def test_a_root_path_from_the_pages_directory(model):
    assert loads(model)[("repo:app/index.html", "repo:app/src/main.ts")] == ("script", "heuristic")


def test_pages_are_shown_by_default(model):
    g = Graph(open_built(*model.split(StringKey("test@0"))))
    assert "repo:site/index.html" not in hidden_by_role(g), "a page's edges would be dropped with it"
