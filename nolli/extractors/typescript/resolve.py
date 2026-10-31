"""TypeScript/JavaScript resolution, run by ``nolli.match``: module specifiers
(``unresolved:js:``) to files (relative paths, tsconfig ``paths``) or external packages;
Node built-ins are dropped like the Python stdlib."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import PurePosixPath

from ...model import USE, Entity, Provenance, Relation
from .. import Context
from ..interfaces import ensure_ext

NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns", "events", "fs", "http", "http2", "https",
    "module", "net", "os", "path", "perf_hooks", "process", "querystring", "readline", "stream", "string_decoder",
    "timers", "tls", "tty", "url", "util", "v8", "vm", "worker_threads", "zlib",
}
JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts", ".json", ".css", ".svg", ".png"]


def tsconfig_aliases(ctx: Context) -> list[tuple[str, str, str]]:
    """(config dir, alias prefix, target prefix) from tsconfig/jsconfig ``paths``."""
    out = []
    for f in ctx.files:
        rel = ctx.rel(f)
        if PurePosixPath(rel).name not in ("tsconfig.json", "jsconfig.json", "tsconfig.app.json"):
            continue
        try:
            text = re.sub(r"//.*?$|/\*.*?\*/", "", ctx.read(rel).decode("utf-8", "replace"), flags=re.S | re.M)
            text = re.sub(r",\s*([}\]])", r"\1", text)
            cfg = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        co = cfg.get("compilerOptions", {})
        base = co.get("baseUrl", ".")
        d = str(PurePosixPath(rel).parent)
        for alias, targets in (co.get("paths") or {}).items():
            if not targets:
                continue
            a = alias[:-1] if alias.endswith("*") else alias
            t = targets[0][:-1] if targets[0].endswith("*") else targets[0]
            out.append((d, a, str(PurePosixPath(d) / base / t)))
    return out


def resolve_imports(ctx: Context) -> None:
    m = ctx.model
    aliases = tsconfig_aliases(ctx)
    files = {e.attrs["path"] for e in m.entities.values() if e.kind == "file"}
    dirs = {e.attrs["path"] for e in m.entities.values() if e.kind == "directory"}

    def norm(p: PurePosixPath) -> str:
        parts: list[str] = []
        for part in p.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        return "/".join(parts)

    def find_file(base: str) -> str | None:
        if base in files:
            return base
        stem = re.sub(r"\.(js|jsx|mjs|cjs)$", "", base)
        for ext in JS_EXTS:
            if stem + ext in files:
                return stem + ext
        if base in dirs:
            for ext in JS_EXTS:
                if f"{base}/index{ext}" in files:
                    return f"{base}/index{ext}"
            return None
        return None

    stats = defaultdict(int)
    new_relations: list[Relation] = []
    for r in m.relations:
        if not (r.kind == USE and r.dst.startswith("unresolved:js:")):
            new_relations.append(r)
            continue
        spec_full = r.dst[len("unresolved:js:"):]
        src_rel = r.src[3:]
        spec = spec_full.split("@" + src_rel)[0] if spec_full.endswith("@" + src_rel) else spec_full
        target: str | None = None
        kind = "external"
        if spec.startswith("."):
            target = find_file(norm(PurePosixPath(src_rel).parent / spec))
            kind = "file" if target else "unresolved"
        elif spec.startswith("/"):
            target = find_file(spec.lstrip("/"))
            kind = "file" if target else "unresolved"
        else:
            for d, a, t in aliases:
                if spec.startswith(a) and (src_rel.startswith(d + "/") or d == "."):
                    target = find_file(norm(PurePosixPath(t + spec[len(a):])))
                    if target:
                        kind = "file"
                        break
            if not target:
                bare = spec.removeprefix("node:")
                pkg = "/".join(bare.split("/")[:2]) if bare.startswith("@") else bare.split("/")[0]
                if pkg in NODE_BUILTINS or spec.startswith("node:"):
                    kind = "stdlib"
                else:
                    target = ensure_ext(m, "js", pkg)
                    kind = "external"
        stats[kind] += 1
        if kind == "stdlib":
            continue
        if kind == "unresolved":
            uid = f"unresolved:js:{spec}"
            if not m.has(uid):
                m.add_entity(Entity(uid, "unresolved", spec, {"language": "javascript"}, Provenance("match")))
            new_relations.append(Relation(USE, r.src, uid, "import", 1, "exact", r.provenance, {"language": "javascript"}))
            continue
        dst = f"fs:{target}" if kind == "file" else target
        if dst != r.src:
            new_relations.append(Relation(USE, r.src, dst, "import", 1, "exact", r.provenance, {"language": "javascript"}))
    m.relations = new_relations
    ctx.notes["js-resolution"] = dict(stats)
