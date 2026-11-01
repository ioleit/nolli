"""TypeScript/JavaScript resolution, run by ``nolli.match``.

``resolve_imports``: module specifiers (``unresolved:js:``) to files, external packages
or nothing (Node built-ins, dropped like the Python stdlib). A specifier is tried as

- a path relative to the importing file (``./x.js`` also finds ``x.ts``, as TypeScript's
  NodeNext resolution does; a folder finds its ``index``), or from the repository root
  (``/x``);
- a tsconfig/jsconfig ``paths`` alias of a config above the importing file (``@/lib``),
  then that config's ``baseUrl`` (``src/lib``); ``extends`` of a config in the
  repository is followed for both;
- a package of the repository's own (a ``package.json`` with that ``name``, as in npm,
  pnpm or yarn workspaces): its source entry, or the subpath inside it;
- else an external package, also from a CDN URL (``https://esm.sh/react@18``).

``resolve_refs``: symbol references (``unresolved:tsref:``) to the class or type they
name, following re-exports (``export { X } from``, ``export * from``) through barrels,
else to the file that defines the name. References into external packages are dropped:
the import edge covers them. Import edges between two files are dropped where
references between them exist, so an edge count means references, as in Python.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import PurePosixPath

from ...model import USE, Entity, Provenance, Relation, address
from .. import Context
from ..interfaces import ensure_ext, ensure_host, is_url, package_name

NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns", "events", "fs", "http", "http2", "https",
    "module", "net", "os", "path", "perf_hooks", "process", "querystring", "readline", "stream", "string_decoder",
    "timers", "tls", "tty", "url", "util", "v8", "vm", "worker_threads", "zlib",
}
JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts", ".json", ".css", ".svg", ".png"]
CONFIG = re.compile(r"^(tsconfig(\.[\w-]+)?|jsconfig)\.json$")
ENTRY_FIELDS = ("source", "types", "typings", "module", "main")  # package.json, most source-like first


def norm(p: PurePosixPath | str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(p).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    return "/".join(parts)


def read_json(ctx: Context, rel: str) -> dict | None:
    """JSON as tsconfig writes it: comments and trailing commas allowed."""
    try:
        text = ctx.read(rel).decode("utf-8", "replace")
        text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda mo: mo.group(1) or "", text, flags=re.S)
        cfg = json.loads(re.sub(r",\s*([}\]])", r"\1", text))
    except (OSError, json.JSONDecodeError):
        return None
    return cfg if isinstance(cfg, dict) else None


class Resolver:
    """Specifier -> file (repository-relative path) for one repository."""

    def __init__(self, ctx: Context):
        m = ctx.model
        self.files = {e.attrs["path"] for e in m.entities.values() if e.kind == "file"}
        self.dirs = {e.attrs["path"] for e in m.entities.values() if e.kind == "directory"}
        rels = [ctx.rel(f) for f in ctx.files]
        self.configs = self._configs(ctx, [r for r in rels if CONFIG.match(PurePosixPath(r).name)])
        self.packages = self._packages(ctx, [r for r in rels if PurePosixPath(r).name == "package.json"])

    @staticmethod
    def _configs(ctx: Context, rels: list[str]) -> list[tuple[str, list[tuple[str, str]], str | None]]:
        """(config dir, [(alias prefix, target prefix)], baseUrl dir), deepest first."""
        raw = {rel: read_json(ctx, rel) or {} for rel in rels}

        def options(rel: str, depth: int = 0) -> dict:
            """compilerOptions with ``extends`` applied; paths and baseUrl relative to the
            config that sets them."""
            cfg = raw.get(rel) or {}
            here = str(PurePosixPath(rel).parent)
            out: dict = {}
            ext = cfg.get("extends")
            for e in ([ext] if isinstance(ext, str) else ext or []):
                if isinstance(e, str) and e.startswith(".") and depth < 5:
                    target = norm(PurePosixPath(here) / e)
                    target = target if target.endswith(".json") else target + ".json"
                    if target in raw:
                        out.update(options(target, depth + 1))
            co = cfg.get("compilerOptions") or {}
            if "baseUrl" in co:
                out["baseUrl"] = norm(PurePosixPath(here) / co["baseUrl"])
            if co.get("paths"):
                out["paths"] = (co["paths"], here)
            return out

        out = []
        for rel in rels:
            opts = options(rel)
            d = str(PurePosixPath(rel).parent)
            aliases = []
            paths, paths_dir = opts.get("paths", ({}, d))
            base = opts.get("baseUrl", paths_dir)
            for alias, targets in paths.items():
                if targets and isinstance(targets, list) and isinstance(targets[0], str):
                    a = alias[:-1] if alias.endswith("*") else alias
                    t = targets[0][:-1] if targets[0].endswith("*") else targets[0]
                    aliases.append((a, norm(PurePosixPath(base) / t) + ("/" if t.endswith("/") else "")))
            if aliases or "baseUrl" in opts:
                out.append((d, sorted(aliases, key=lambda x: -len(x[0])), opts.get("baseUrl")))
        return sorted(out, key=lambda c: -len(c[0]))

    def _packages(self, ctx: Context, rels: list[str]) -> dict[str, tuple[str, str | None]]:
        """The repository's own packages: name -> (dir, entry file)."""
        out = {}
        for rel in rels:
            if "node_modules" in rel:
                continue
            pj = read_json(ctx, rel) or {}
            name, d = pj.get("name"), str(PurePosixPath(rel).parent)
            if not isinstance(name, str) or not name or d == "." and not pj.get("workspaces"):
                continue
            entry = None
            exp = pj.get("exports")
            cands = [exp] if isinstance(exp, str) else []
            if isinstance(exp, dict):
                dot = exp.get(".", exp)
                cands += [dot] if isinstance(dot, str) else [v for k, v in (dot.items() if isinstance(dot, dict) else []) if isinstance(v, str)]
            cands += [pj[f] for f in ENTRY_FIELDS if isinstance(pj.get(f), str)]
            for c in cands:
                entry = self.find(norm(PurePosixPath(d) / c)) or self.find(norm(PurePosixPath(d) / re.sub(r"^(\./)?(dist|build|lib|out)/", "src/", c)))
                if entry:
                    break
            out[name] = (d, entry or self.find(f"{d}/src") or self.find(d))
        return out

    def find(self, base: str) -> str | None:
        """The file a path names: as written, with a TS/JS extension (``x.js`` written for
        ``x.ts``), or a folder's index."""
        base = base if base != "." else ""
        if base in self.files:
            return base
        stem = re.sub(r"\.(js|jsx|mjs|cjs)$", "", base)
        for ext in JS_EXTS:
            if stem + ext in self.files:
                return stem + ext
        if base in self.dirs or base == "":
            for ext in JS_EXTS:
                if f"{base}/index{ext}".lstrip("/") in self.files:
                    return f"{base}/index{ext}".lstrip("/")
        return None

    def resolve(self, spec: str, src_rel: str) -> tuple[str, str | None]:
        """(how, target): ("file", path), ("external", package name), ("stdlib", None) or
        ("unresolved", None)."""
        if spec.startswith("."):
            f = self.find(norm(PurePosixPath(src_rel).parent / spec))
            return ("file", f) if f else ("unresolved", None)
        if spec.startswith("/"):
            f = self.find(norm(spec))
            return ("file", f) if f else ("unresolved", None)
        for d, aliases, base in self.configs:
            if d != "." and not src_rel.startswith(d + "/"):
                continue
            for a, t in aliases:
                if spec.startswith(a):
                    f = self.find(norm(t + spec[len(a):]))
                    if f:
                        return "file", f
            if base is not None:
                f = self.find(norm(PurePosixPath(base) / spec))
                if f:
                    return "file", f
        pkg = package_name(spec)
        if pkg in self.packages:
            d, entry = self.packages[pkg]
            sub = spec[len(pkg):].lstrip("/")
            f = (self.find(f"{d}/{sub}") or self.find(f"{d}/src/{sub}")) if sub else entry
            if f:
                return "file", f
        if pkg in NODE_BUILTINS or spec.startswith("node:"):
            return "stdlib", None
        return "external", pkg

    def file(self, spec: str, src_rel: str) -> str | None:
        how, target = self.resolve(spec, src_rel)
        return target if how == "file" else None


def resolve_imports(ctx: Context) -> None:
    m = ctx.model
    res = Resolver(ctx)
    stats: dict[str, int] = defaultdict(int)
    new_relations: list[Relation] = []
    for r in m.relations:
        key = address.unresolved_key(r.dst, "js") if r.kind == USE else None
        if key is None:
            new_relations.append(r)
            continue
        src_rel = address.path_of(r.src)
        spec = key.removesuffix("@" + src_rel)
        how, target = res.resolve(spec, src_rel)
        stats[how] += 1
        attrs = dict(r.attrs, language="javascript")
        if how == "stdlib":
            continue
        if how == "unresolved":
            dst = address.unresolved("js", spec)
            if not m.has(dst):
                m.add_entity(Entity(dst, "unresolved", spec, {"language": "javascript"}, Provenance("match")))
        else:
            dst = ctx.repo_id(target) if how == "file" else ensure_host(m, target, "match") if is_url(spec) else ensure_ext(m, "js", target)
        if dst != r.src:
            new_relations.append(Relation(USE, r.src, dst, "import", 1, "exact", r.provenance, attrs))
    m.relations = new_relations
    ctx.notes["js-resolution"] = dict(stats)


def resolve_refs(ctx: Context) -> None:
    m = ctx.model
    res = Resolver(ctx)
    exports = ctx.notes.get("ts-exports", {})

    def symbol_in(file: str, name: str, depth: int = 0) -> str | None:
        """The entity ``name`` stands for in ``file``: its symbol, the symbol or file a
        re-export leads to, or the file itself if it defines the name (a function)."""
        sid = address.part(ctx.repo_id(file), name)
        if m.has(sid):
            return sid
        ex = exports.get(file)
        if ex is None or depth > 5:
            return None
        local = ex["local"].get(name)
        if local and local != name:
            return symbol_in(file, local, depth + 1)
        for exported, spec, orig in ex["reexports"]:
            if exported == name:
                f = res.file(spec, file)
                if f is None:
                    return None
                return (symbol_in(f, orig, depth + 1) if orig else None) or ctx.repo_id(f)
        if name in ex["names"]:
            return ctx.repo_id(file)
        for exported, spec, _ in ex["reexports"]:
            if exported == "*":
                f = res.file(spec, file)
                hit = symbol_in(f, name, depth + 1) if f else None
                if hit:
                    return hit
        return None

    stats: dict[str, int] = defaultdict(int)
    new_relations: list[Relation] = []
    for r in m.relations:
        if not (r.kind == USE and address.unresolved_key(r.dst, "tsref") is not None):
            new_relations.append(r)
            continue
        ph = m.entities[r.dst].attrs
        f = res.file(ph["spec"], ph["from"])
        if f is None:
            stats["external"] += r.count
            continue
        dst = (symbol_in(f, ph["name"]) if ph["name"] else None) or ctx.repo_id(f)
        if dst == r.src:
            stats["self"] += r.count
            continue
        stats["symbol" if address.is_part(dst) else "file"] += r.count
        new_relations.append(Relation(USE, r.src, dst, "ref", r.count, "exact", r.provenance, dict(r.attrs)))
    m.relations = new_relations

    def file_of(eid: str) -> str | None:
        if address.is_part(eid):
            return address.file_of(eid)
        return eid if address.is_repo(eid) and m.entities[eid].kind == "file" else None

    covered = set()
    for r in m.relations:
        if r.kind == USE and r.subkind == "ref" and (address.is_part(r.src) or address.is_part(r.dst) or r.attrs.get("language") == "javascript"):
            a, b = file_of(r.src), file_of(r.dst)
            if a and b:
                covered.add((a, b))
    before = len(m.relations)
    m.relations = [r for r in m.relations if not (r.kind == USE and r.subkind == "import" and r.attrs.get("language") == "javascript"
                                                  and (r.src, r.dst) in covered)]
    stats["imports_replaced"] = before - len(m.relations)
    ctx.notes["tsref-resolution"] = dict(stats)
