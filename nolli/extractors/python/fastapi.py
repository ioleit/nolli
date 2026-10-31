"""HTTP endpoints provided by Python web code (FastAPI/Starlette style).

Finds ``@<router>.<method>("<path>")`` decorators, ``APIRouter(prefix=...)``
declarations and ``<parent>.include_router(<child>, prefix=...)`` calls, and
composes full paths by following include chains from ``FastAPI()`` apps.
Each route is reported to the shared API structure (``extractors/api.py``) as an
operation of the app it is mounted in, provided by the file that defines the
handler. Routers that are never included anywhere are APIs of their own (their
prefix stands alone), marked heuristic.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .. import Context, api
from ...match import module_candidates
from .imports import module_name_of

METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "websocket", "api_route"}


@dataclass
class RouterVar:
    file: str
    name: str
    prefix: str = ""
    is_app: bool = False
    routes: list[tuple[str, str, int, str]] = field(default_factory=list)  # method, path, line, handler
    includes: list[tuple[str, str, int]] = field(default_factory=list)  # (child key or raw text, prefix, line)
    included_by: int = 0


def const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def kw(call: ast.Call, name: str) -> ast.expr | None:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def run(ctx: Context) -> None:
    m = ctx.model
    routers: dict[tuple[str, str], RouterVar] = {}
    imports_by_file: dict[str, dict[str, tuple[str, str | None]]] = {}  # file -> alias -> (module, name)
    module_to_file: dict[str, str] = {}
    for f in ctx.files:
        rel = ctx.rel(f)
        if rel.endswith(".py") and m.has(ctx.fs_id(rel)):
            module_to_file[module_name_of(rel)] = rel

    for rel in ctx.progress.each(module_to_file.values()):
        if m.entities[ctx.fs_id(rel)].attrs.get("role") == "test":
            continue
        try:
            tree = ast.parse(ctx.read(rel), filename=rel)
        except (SyntaxError, ValueError):
            continue
        aliases: dict[str, tuple[str, str | None]] = {}
        pkg_parts = rel.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    mod = node.module or ""
                else:
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                    mod = ".".join(base + ([node.module] if node.module else []))
                if mod:
                    for a in node.names:
                        aliases[a.asname or a.name] = (mod, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    aliases[a.asname or a.name.split(".")[0]] = (a.name, None)
        imports_by_file[rel] = aliases

        for node in ast.walk(tree):
            # router = APIRouter(prefix="/x"); app = FastAPI()
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fn = node.value.func
                fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if fname in ("APIRouter", "FastAPI", "Starlette", "Flask", "Blueprint") and node.targets:
                    t = node.targets[0]
                    if isinstance(t, ast.Name):
                        pfx = const_str(kw(node.value, "prefix") or kw(node.value, "url_prefix")) or ""
                        routers[(rel, t.id)] = RouterVar(rel, t.id, pfx, fname in ("FastAPI", "Starlette", "Flask"))
            # <x>.include_router(<y>, prefix="/p") / <x>.register_blueprint(<y>, url_prefix=...)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("include_router", "register_blueprint") and isinstance(node.func.value, ast.Name):
                    parent = (rel, node.func.value.id)
                    routers.setdefault(parent, RouterVar(rel, node.func.value.id))
                    child = node.args[0] if node.args else None
                    pfx = const_str(kw(node, "prefix") or kw(node, "url_prefix")) or ""
                    raw = ast.unparse(child) if child is not None else "?"
                    routers[parent].includes.append((raw, pfx, node.lineno))
            # @router.get("/path")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in METHODS:
                        if isinstance(d.func.value, ast.Name) and d.args:
                            path = const_str(d.args[0])
                            if path is None:
                                continue
                            method = d.func.attr.upper()
                            if d.func.attr == "api_route":
                                methods_kw = kw(d, "methods")
                                method = ",".join(sorted(const_str(e) or "?" for e in getattr(methods_kw, "elts", []))) or "ANY"
                            key = (rel, d.func.value.id)
                            routers.setdefault(key, RouterVar(rel, d.func.value.id))
                            routers[key].routes.append((method, path, d.lineno, node.name))

    # Resolve include targets to router keys via the including file's imports.
    def find_module(mod: str, file: str) -> str | None:
        for cand in module_candidates(mod, file):
            if cand in module_to_file:
                return module_to_file[cand]
        return None

    def resolve_name(file: str, name: str, depth: int = 0) -> tuple[str, str] | None:
        """(file, variable) that ``name`` in ``file`` refers to, following re-exports."""
        if (file, name) in routers:
            return (file, name)
        if depth > 5:
            return None
        aliases = imports_by_file.get(file, {})
        if name in aliases:
            mod, orig = aliases[name]
            f = find_module(mod, file)
            if f and orig:
                return resolve_name(f, orig, depth + 1)
        return None

    def resolve_child(file: str, raw: str) -> tuple[str, str] | None:
        aliases = imports_by_file.get(file, {})
        if "." in raw:
            mod_alias, attr = raw.rsplit(".", 1)
            if mod_alias in aliases:
                mod, name = aliases[mod_alias]
                f = find_module(f"{mod}.{name}" if name else mod, file)
                if f:
                    return resolve_name(f, attr)
            return None
        return resolve_name(file, raw)

    resolved_includes: dict[tuple[str, str], list[tuple[tuple[str, str], str]]] = {}
    for key, rv in routers.items():
        for raw, pfx, _line in rv.includes:
            child = resolve_child(rv.file, raw)
            if child:
                resolved_includes.setdefault(key, []).append((child, pfx))
                routers[child].included_by += 1
            else:
                ctx.notes.setdefault("http-routes-unresolved-includes", []).append((rv.file, raw))

    def api_of(key: tuple[str, str]) -> tuple[str, str, str]:
        """(key, file, about) of the API a root serves: the app, or a router nobody mounts."""
        rv = routers[key]
        what = "app" if rv.is_app else "router, never mounted,"
        return f"{rv.file}:{rv.name}", rv.file, f"FastAPI {what} `{rv.name}` in {rv.file}"

    def emit(key: tuple[str, str], api_key: tuple[str, str], base: str, exact: bool, depth: int = 0) -> None:
        rv = routers[key]
        here = base + rv.prefix
        api_id, api_file, api_about = api_of(api_key)
        for method, path, line, handler in rv.routes:
            api.provide(ctx, api.Operation("http", api_id, api_file, api_about, method, (here + path) or "/", rv.file, line,
                                           handler, exact, "python-fastapi"))
        if depth > 10:
            return
        for child, pfx in resolved_includes.get(key, []):
            emit(child, api_key, here + pfx, exact, depth + 1)

    roots = [k for k, rv in routers.items() if rv.is_app or rv.included_by == 0]
    for key in sorted(roots):
        emit(key, key, "", routers[key].is_app)
    ctx.notes["http-routers"] = {f"{k[0]}:{k[1]}": {"prefix": v.prefix, "app": v.is_app, "routes": len(v.routes), "included_by": v.included_by} for k, v in routers.items()}
