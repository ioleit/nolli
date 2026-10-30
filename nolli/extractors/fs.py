"""Artifacts: the directory tree of tracked files, as the ``contain`` hierarchy.

Files get a ``language`` (by extension) and a ``role``: ``source``, ``test``,
``config``, ``docs``, ``data`` or ``asset``. Roles are attributes, not
filters: views decide what to hide, and report what they hid.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..model import Entity, Provenance
from . import Context

LANG_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
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


def classify(rel: str) -> tuple[str | None, str]:
    p = PurePosixPath(rel)
    parts = set(p.parts[:-1])
    ext = p.suffix.lower()
    lang = LANG_BY_EXT.get(ext)
    if p.name in CONFIG_FILES or p.name.startswith("Dockerfile") or CONFIG_CODE.search(p.name):
        return lang, "config"
    if parts & TEST_DIRS or TEST_FILE.search(p.name):
        return lang, "test"
    if lang in ("html", "css"):
        return lang, "markup"  # part of a frontend, but carries no dependencies we extract
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
    root = m.add_entity(Entity("fs:.", "directory", ctx.repo.name, {"path": ".", "root": True}, Provenance("fs")))
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
                d = m.add_entity(Entity(ctx.fs_id(acc), "directory", part, {"path": acc}, Provenance("fs", acc)))
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
        e = m.add_entity(Entity(ctx.fs_id(rel), "file", f.name, attrs, Provenance("fs", rel)))
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
