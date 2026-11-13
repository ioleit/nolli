"""How nolli's modules import each other (Google's Python style guide, section 2.2, with
classes allowed): a module names the package it imports from (no relative imports), and
uses a function or constant of another nolli module through that module
(``projection.fold``, ``schema.SYMBOL_KINDS``), so a call site says whose ``fold`` it is.
Classes and type aliases (``Scene``, ``Key``) may be imported by name. A package's
``__init__`` may re-export what its own modules define (``projection.fold`` is
``projection._core.fold``), so its callers name the package.

The command line and the server are not held to it yet."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOT_YET = {"nolli/cli.py", "nolli/server"}


def list_checked() -> list[Path]:
    return [p for p in sorted((ROOT / "nolli").rglob("*.py"))
            if not any(str(p.relative_to(ROOT)).startswith(n) for n in NOT_YET)]


def is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def is_type(name: str) -> bool:
    return name[:1].isupper() and not name.isupper()


def test_no_relative_imports():
    found = [f"{p.relative_to(ROOT)}:{n.lineno}" for p in list_checked()
             for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.ImportFrom) and n.level]
    assert not found, f"import from nolli.…, not from .: {found}"


def test_functions_and_constants_are_used_through_their_module():
    found = []
    for p in list_checked():
        package = ".".join(p.relative_to(ROOT).parent.parts) if p.name == "__init__.py" else None
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "nolli" \
                    and not (package and n.module.startswith(package + ".")):  # a package re-exporting its own
                found += [f"{p.relative_to(ROOT)}:{n.lineno} {a.name}" for a in n.names
                          if not is_type(a.name) and not is_module(f"{n.module}.{a.name}")]
    assert not found, f"import the module and call module.name: {found}"

