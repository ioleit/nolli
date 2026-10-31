"""TypeScript/JavaScript front-end: modules and imports, classes and types and references
to them, and library adapters that read TS/JS syntax (fetch/axios/EventSource calls,
environment reads). JavaScript is read by the same code: it is TypeScript without the
type syntax."""

from __future__ import annotations

from .. import Language
from . import describe, env, fetch, imports, resolve, symbols

LANGUAGE = Language(
    name="typescript",
    extensions={".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
                ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript"},
    parse=imports.parse,
    extractors=[("typescript-imports", imports.run), ("typescript-symbols", symbols.run),
                ("typescript-fetch", fetch.run), ("typescript-env", env.run)],
    resolvers=[("typescript-imports", resolve.resolve_imports), ("typescript-refs", resolve.resolve_refs)],
    describe=describe,
    package_body=("index.ts", "index.tsx", "index.mts", "index.js", "index.jsx", "index.mjs"),
    symbols={"class": "C", "interface": "I", "type": "T", "enum": "E"},
)
