"""Python resolution, run by ``nolli.match``: import placeholders (``unresolved:py:``) to
files, the stdlib (dropped) or external packages, and symbol references
(``unresolved:pyref:``) to classes, following re-exports, else to the module's file."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import PurePosixPath

from ...model import USE, Relation
from .. import Context
from ..interfaces import ensure_ext

SOURCE_ROOTS = ("src", "lib", "python")


def python_module_map(m) -> dict[str, str]:
    """dotted module -> entity id (file or directory for namespace packages)."""
    out: dict[str, str] = {}
    for e in m.entities.values():
        if e.kind == "file" and e.attrs.get("language") == "python":
            rel = e.attrs["path"]
            parts = rel[:-3].split("/") if rel.endswith(".py") else None
            if not parts:
                continue
            if parts[-1] == "__init__":
                out[".".join(parts[:-1])] = e.id
            else:
                out.setdefault(".".join(parts), e.id)
        elif e.kind == "directory" and e.attrs.get("language") == "python":
            out.setdefault(e.attrs["path"].replace("/", "."), e.id)
    out.pop("", None)
    # src-layout: ``src/megatron/bridge`` is imported as ``megatron.bridge``
    for root in SOURCE_ROOTS:
        for k, v in list(out.items()):
            if k.startswith(root + ".") and k[len(root) + 1:] not in out:
                out[k[len(root) + 1:]] = v
    return out


def module_candidates(mod: str, from_rel: str) -> list[str]:
    """Dotted names to try for ``mod`` imported from file ``from_rel``: as written, then
    relative to each ancestor directory of the importing file (a package run with its own
    directory as source root imports ``routers.x`` from ``server/routers/x.py``)."""
    out = [mod]
    parts = PurePosixPath(from_rel).parent.parts
    for i in range(len(parts), 0, -1):
        out.append(".".join(parts[:i]) + "." + mod)
    return out


def resolve_module(mod: str, from_rel: str, modmap: dict[str, str]) -> str | None:
    for cand in module_candidates(mod, from_rel):
        parts = cand.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in modmap:
                # an ancestor-relative candidate must match beyond the ancestor itself
                if cand != mod and i <= len(cand.split(".")) - len(mod.split(".")):
                    break
                return modmap[prefix]
    return None


def resolve_imports(ctx: Context) -> None:
    m = ctx.model
    modmap = python_module_map(m)
    stdlib = set(sys.stdlib_module_names)
    rewrites: list[tuple[Relation, str | None, str]] = []  # (relation, new dst or None, status)
    for r in m.relations:
        if r.kind != USE or not r.dst.startswith("unresolved:py:"):
            continue
        mod = r.dst[len("unresolved:py:"):]
        parts = mod.split(".")
        target = resolve_module(mod, r.src[3:], modmap)
        if target:
            rewrites.append((r, target, "file"))
        elif parts[0] in stdlib or parts[0] in ("__future__",):
            rewrites.append((r, None, "stdlib"))
        else:
            rewrites.append((r, ensure_ext(m, "py", parts[0]), "external"))

    # ``from a.b import c`` produced both a.b and a.b.c; keep the most specific resolution per line.
    by_line: dict[tuple[str, int | None], list[tuple[Relation, str | None, str]]] = defaultdict(list)
    for item in rewrites:
        r = item[0]
        by_line[(r.src, r.provenance.line if r.provenance else None)].append(item)
    drop: set[int] = set()
    for items in by_line.values():
        files = [it for it in items if it[2] == "file"]
        if len(files) > 1:
            # prefer targets that are not __init__/directories when a sibling resolved deeper
            deep = [it for it in files if not (it[1].endswith("/__init__.py") or m.entities[it[1]].kind == "directory")]
            if deep:
                for it in files:
                    if it not in deep:
                        drop.add(id(it[0]))
    new_relations: list[Relation] = []
    for r in m.relations:
        if r.kind == USE and r.dst.startswith("unresolved:py:"):
            continue
        new_relations.append(r)
    seen: set[tuple] = set()
    for r, dst, status in rewrites:
        if id(r) in drop or dst is None or dst == r.src:
            continue
        key = (r.src, dst, r.provenance.line if r.provenance else None)
        if key in seen:
            continue
        seen.add(key)
        new_relations.append(Relation(USE, r.src, dst, "import", 1, "exact", r.provenance, {"language": "python"}))
    m.relations = new_relations
    ctx.notes["py-resolution"] = {"file": sum(1 for _, _, s in rewrites if s == "file"), "external": sum(1 for _, _, s in rewrites if s == "external"), "stdlib": sum(1 for _, _, s in rewrites if s == "stdlib")}


def exact_module(mod: str, from_rel: str, modmap: dict[str, str]) -> str | None:
    for cand in module_candidates(mod, from_rel):
        if cand in modmap:
            return modmap[cand]
    return None


def resolve_refs(ctx: Context) -> None:
    """Bind symbol references to symbols (following re-exports), else to the module's file.
    References into external modules are dropped: the import edge covers them. Import
    edges between two files are dropped where references between them exist, so an edge
    count means references, not import statements."""
    m = ctx.model
    modmap = python_module_map(m)
    aliases = ctx.notes.get("py-aliases", {})
    symbols = {e.id for e in m.entities.values() if e.kind == "class"}

    def symbol_in(file_rel: str, name: str, depth: int = 0) -> str | None:
        sid = f"py:{file_rel}#{name}"
        if sid in symbols:
            return sid
        al = aliases.get(file_rel, {}).get(name)
        if al and depth < 4:
            f = exact_module(al[0], file_rel, modmap) or resolve_module(al[0], file_rel, modmap)
            if f and m.entities[f].kind == "file":
                return symbol_in(f[3:], al[1], depth + 1) or exact_module(f"{al[0]}.{al[1]}", file_rel, modmap) or f
            if f:
                return exact_module(f"{al[0]}.{al[1]}", file_rel, modmap) or f
        return None

    def resolve(key: str, src_rel: str) -> str | None:
        mod, _, name = key.partition(":")
        if name:
            f = exact_module(mod, src_rel, modmap) or resolve_module(mod, src_rel, modmap)
            if not f:
                return None
            if m.entities[f].kind == "file":
                return symbol_in(f[3:], name) or exact_module(f"{mod}.{name}", src_rel, modmap) or f
            return exact_module(f"{mod}.{name}", src_rel, modmap) or f
        parts = mod.split(".")
        for i in range(len(parts), 0, -1):
            f = exact_module(".".join(parts[:i]), src_rel, modmap)
            if f:
                rest = parts[i:]
                if rest and m.entities[f].kind == "file":
                    return symbol_in(f[3:], rest[0]) or f
                return f
        return None

    stats = defaultdict(int)
    new_relations: list[Relation] = []
    for r in m.relations:
        if not (r.kind == USE and r.dst.startswith("unresolved:pyref:")):
            new_relations.append(r)
            continue
        src_rel = r.src.split("#")[0][3:] if r.src.startswith("py:") else r.src[3:]
        dst = resolve(r.dst[len("unresolved:pyref:"):], src_rel)
        if dst is None:
            stats["external"] += r.count
        elif dst == r.src:
            stats["self"] += r.count
        else:
            stats["symbol" if dst in symbols else "file"] += r.count
            new_relations.append(Relation(USE, r.src, dst, "ref", r.count, "exact", r.provenance, dict(r.attrs)))
    m.relations = new_relations

    def file_of(eid: str) -> str | None:
        if eid.startswith("py:"):
            return "fs:" + eid.split("#")[0][3:]
        return eid if eid.startswith("fs:") and m.entities[eid].kind == "file" else None

    covered = set()
    for r in m.relations:
        if r.kind == USE and r.subkind == "ref":
            a, b = file_of(r.src), file_of(r.dst)
            if a and b:
                covered.add((a, b))
    before = len(m.relations)
    m.relations = [r for r in m.relations if not (r.kind == USE and r.subkind == "import" and r.attrs.get("language") == "python" and (r.src, r.dst) in covered)]
    stats["imports_replaced"] = before - len(m.relations)
    ctx.notes["pyref-resolution"] = dict(stats)
