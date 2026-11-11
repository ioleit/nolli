"""A module-level function that no other module of nolli uses starts with ``_`` (tests don't
count: they may reach into a module). nolli's own model has no functions, so this reads the
syntax tree: a use is ``module.name`` on an imported module, or ``from module import name``.

The command line and the server are not held to it yet."""

from __future__ import annotations

import ast
import importlib.util
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOT_YET = ("nolli/cli.py", "nolli/server")


def _name_module(p: Path) -> str:
    return ".".join(p.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")


def _is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _resolve_relative(mod: str, is_package: bool, level: int, module: str | None) -> str:
    parts = mod.split(".")
    base = parts[: len(parts) - level + (1 if is_package else 0)]
    return ".".join(base + ([module] if module else []))


def _list_uses(p: Path) -> set[tuple[str, str]]:
    """(module, name) of what ``p`` uses from imported modules."""
    tree, bound, out = ast.parse(p.read_text()), {}, set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            module = _resolve_relative(_name_module(p), p.name == "__init__.py", n.level, n.module) if n.level else n.module
            for a in n.names:
                if _is_module(f"{module}.{a.name}"):
                    bound[a.asname or a.name] = f"{module}.{a.name}"
                else:
                    out.add((module, a.name))
        elif isinstance(n, ast.Import):
            for a in n.names:
                bound[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            chain, v = [], n
            while isinstance(v, ast.Attribute):
                chain.append(v.attr)
                v = v.value
            if isinstance(v, ast.Name) and v.id in bound:
                module = bound[v.id]
                for a in reversed(chain):
                    if not _is_module(f"{module}.{a}"):
                        out.add((module, a))
                        break
                    module = f"{module}.{a}"
    return out


def test_functions_only_used_in_their_module_are_private():
    used = defaultdict(set)
    for p in [*(ROOT / "nolli").rglob("*.py"), *(ROOT / "dev").rglob("*.py")]:
        for module, name in _list_uses(p):
            used[(module, name)].add(_name_module(p))
    found = []
    for p in sorted((ROOT / "nolli").rglob("*.py")):
        rel, module = str(p.relative_to(ROOT)), _name_module(p)
        if rel.startswith(NOT_YET):
            continue
        found += [f"{rel}:{n.lineno} {n.name}" for n in ast.parse(p.read_text()).body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
                  and not used[(module, n.name)] - {module}]
    assert not found, f"only used in their module, so _name: {found}"
