"""How the picture names things: shared by the scene and the hover descriptions."""

from __future__ import annotations

from .model import Model
from .rollup import display_path, is_init

MODULE_LANGS = ("python", "typescript", "javascript")


def is_body(x: str) -> bool:
    return x.startswith("body:")


def label_of(m: Model, x: str, expanded: set[str]) -> str:
    if is_body(x):
        return ""
    e = m.entities[x]
    if e.kind == "directory":
        return e.name if e.attrs.get("root") else display_path(m, x, expanded)
    if e.kind == "file" and e.attrs.get("language") in MODULE_LANGS:
        return e.name.rsplit(".", 1)[0] if "." in e.name else e.name  # a source file is its module
    if e.kind == "service":
        return "svc " + e.name
    if e.kind == "image":
        return "img " + e.name.removesuffix("/Dockerfile")
    return e.name


def leaf_count(m: Model, x: str) -> int:
    """Files below a directory; leaves below anything else."""
    is_dir = m.entities[x].kind == "directory"
    n, stack = 0, list(m.children(x))
    while stack:
        c = stack.pop()
        k = m.entities[c].kind
        if is_dir:
            if k == "file":
                n += 0 if is_init(m, c) else 1
            elif k == "directory":
                stack.extend(m.children(c))
        elif not m.children(c):
            n += 1
        else:
            stack.extend(m.children(c))
    return n
