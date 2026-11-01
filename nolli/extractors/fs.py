"""Artifacts: the directory tree of tracked files, as the ``contain`` hierarchy.

Files get a ``language`` (by extension) and a ``role``: ``source``, ``test``,
``config``, ``docs``, ``data`` or ``asset``. Roles are attributes, not
filters: views decide what to hide, and report what they hid.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..model import Entity, Provenance, address
from . import Context, languages

# Languages nolli only names (their files are drawn, not read); the extracted languages
# add their own extensions (``Language.extensions``, see ``lang_by_ext``).
OTHER_LANGS = {
    ".pyi": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".less": "css",
    ".scss": "css",
}
CONFIG_EXT = {".toml", ".ini", ".cfg", ".yaml", ".yml", ".json", ".lock", ".env"}
DOCS_EXT = {".md", ".mdx", ".rst", ".txt"}
DATA_EXT = {".csv", ".jsonl", ".parquet", ".xml", ".geojson"}
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".mp3", ".mp4", ".ogv", ".woff", ".woff2", ".ttf", ".flac"}

TEST_DIRS = {"tests", "test", "__tests__", "e2e", "spec", "specs", "testing", "test-results"}
TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$|^conftest\.py$)")
GENERATED_DIRS = {"node_modules", "dist", "build", "__pycache__", ".venv", "venv", "target", ".git"}
CONFIG_FILES = {"Dockerfile", "Procfile", "Makefile", ".gitignore", ".gitattributes", ".pre-commit-config.yaml", "setup.py", "conftest.py"}
# Code files that configure tooling rather than implement the system.
CONFIG_CODE = re.compile(r"(^|\.)(config|conf|rc)\.[cm]?[jt]sx?$|^\.(eslintrc|prettierrc|babelrc)|^(vite|vitest|jest|tailwind|postcss|eslint|babel|webpack|rollup|playwright|next|nuxt|svelte|astro)\.config\.")


def lang_by_ext() -> dict[str, str]:
    return OTHER_LANGS | {ext: v for lang in languages() for ext, v in lang.extensions.items()}


def module_facts(name: str, lang: str | None) -> dict:
    """What the graph operations need to know about a file of an extracted language:
    it is a ``module``; a package body (``__init__.py``, ``index.ts``) is also ``body``,
    what it makes its folder ("package", "folder module")."""
    for language in languages():
        if lang in language.names:
            return {"module": True} | ({"body": language.package} if name in language.package_body else {})
    return {}


MAX_COUNTED = 4 << 20  # don't read files over 4 MiB just to count their lines


def count_lines(path, size: int) -> int | None:
    """Lines in a text file (a last line without a newline counts); None if unreadable."""
    if size > MAX_COUNTED:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None  # binary
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def classify(rel: str) -> tuple[str | None, str]:
    p = PurePosixPath(rel)
    parts = set(p.parts[:-1])
    ext = p.suffix.lower()
    lang = lang_by_ext().get(ext)
    if p.name in CONFIG_FILES or p.name.startswith("Dockerfile") or CONFIG_CODE.search(p.name):
        return lang, "config"
    if parts & TEST_DIRS or TEST_FILE.search(p.name):
        return lang, "test"
    if lang in ("html", "css"):
        return lang, "markup"  # pages and stylesheets; what they load is read by extractors/markup
    if lang:
        return lang, "source"
    if ext in CONFIG_EXT or p.name.startswith("."):
        return None, "config"
    if ext in DOCS_EXT:
        return None, "docs"
    if ext in DATA_EXT:
        return None, "data"
    if ext in ASSET_EXT:
        return None, "asset"
    return None, "other"


def run(ctx: Context) -> None:
    m = ctx.model
    root = m.add_entity(Entity(address.repo(""), "directory", ctx.repo.name, {"path": ".", "root": True}, Provenance("fs")))
    seen_dirs = {".": root.id}
    for f in ctx.files:
        rel = ctx.rel(f)
        if set(f.parts) & GENERATED_DIRS:
            continue
        parent_id = root.id
        acc = ""
        for part in f.parts[:-1]:
            acc = f"{acc}/{part}" if acc else part
            if acc not in seen_dirs:
                d = m.add_entity(Entity(address.repo(acc), "directory", part, {"path": acc}, Provenance("fs", acc)))
                m.contain(parent_id, d.id, "fs")
                seen_dirs[acc] = d.id
            parent_id = seen_dirs[acc]
        lang, role = classify(rel)
        try:
            size = (ctx.repo / f).stat().st_size
        except OSError:
            size = 0
        attrs = {"path": rel, "role": role, "size": size}
        if lang:
            attrs["language"] = lang
            attrs.update(module_facts(f.name, lang))
            lines = count_lines(ctx.repo / f, size)
            if lines is not None:
                attrs["lines"] = lines
        e = m.add_entity(Entity(address.repo(rel), "file", f.name, attrs, Provenance("fs", rel)))
        m.contain(parent_id, e.id, "fs")

    # Directory roles, by bytes: a directory is "source" (or "mixed", when other roles sit
    # next to it) if source is at least a tenth of its content, so a docs tree with one
    # conf.py stays docs; otherwise it takes the dominant role. Dot-directories (.github,
    # .vscode) are tooling and count as config whatever they hold.
    def dir_role(did: str) -> dict[str, int]:
        by_role: dict[str, int] = {}
        for c in m.children(did):
            ce = m.entities[c]
            if ce.kind == "directory":
                for r, b in dir_role(c).items():
                    by_role[r] = by_role.get(r, 0) + b
            else:
                r = ce.attrs.get("role", "other")
                by_role[r] = by_role.get(r, 0) + max(1, ce.attrs.get("size", 0))
        total = sum(by_role.values()) or 1
        src = by_role.get("source", 0)
        if m.entities[did].name.startswith(".") and not m.entities[did].attrs.get("root"):
            role = "config"
        elif src >= 0.1 * total:
            role = "source" if set(by_role) <= {"source"} else "mixed"
        elif by_role:
            role = max(by_role.items(), key=lambda kv: (kv[1], kv[0]))[0]
        else:
            role = "other"
        m.entities[did].attrs["role"] = role
        return by_role

    dir_role(root.id)
    # Language of a directory: the dominant language of its source files, by size.
    for did, e in m.entities.items():
        if e.kind != "directory":
            continue
        by_lang: dict[str, int] = {}
        for c in m.descendants(did):
            ce = m.entities[c]
            if ce.kind == "file" and ce.attrs.get("language") and ce.attrs.get("role") == "source":
                by_lang[ce.attrs["language"]] = by_lang.get(ce.attrs["language"], 0) + ce.attrs.get("size", 0)
        if by_lang:
            e.attrs["language"] = max(by_lang.items(), key=lambda kv: kv[1])[0]
            e.attrs["languages"] = dict(sorted(by_lang.items()))
