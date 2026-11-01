"""How the picture names things: shared by the scene and the hover descriptions.

Every box is named the same way: zero or more small icons that say what it is (``D``
directory, ``m`` module, the language's symbol icons (``C`` class, ``I`` interface, ``T``
type, ``E`` enum), ``svc``/``img`` deployment, ``API``, ``/`` a path step
of an API, the verbs of an operation), then a readable name (``icons_of``, ``name_of``).
Text that has no icons (hover descriptions, residue) uses ``label_of``."""

from __future__ import annotations

from .model import address
from .model import Model
from .rollup import display_path, is_init


SYMBOL_ICON = {"class": "C", "interface": "I", "type": "T", "enum": "E", "function": "f"}  # model.SYMBOL_KINDS


def is_module(e) -> bool:
    """A file of an extracted language (the file tree marks it): drawn ``m``, unfoldable."""
    return e.kind == "file" and bool(e.attrs.get("module"))


def module_stem(file_name: str) -> str:
    """A source file's module name: without its extension, ``.d.ts`` included."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return stem[:-2] if stem.endswith(".d") and file_name.endswith(("ts", "mts", "cts")) else stem


VERB_ICON = {"WEBSOCKET": "WS", "DELETE": "DEL", "OPTIONS": "OPT"}


def icons_of(m: Model, x: str) -> list[str]:
    if address.is_own_code(x):
        return []
    e = m.entities[x]
    if e.kind == "directory":
        return ["D"]
    if is_module(e):
        return ["m"]
    if e.kind in SYMBOL_ICON:
        return [SYMBOL_ICON[e.kind]]
    if e.kind == "service":
        return ["svc"]
    if e.kind == "image":
        return ["img"]
    if e.kind == "api":
        return ["API"]
    if e.kind == "resource":
        return ["/"]
    if e.kind == "endpoint":
        return [VERB_ICON.get(v, v) for v in e.attrs.get("method", "").split(",") if v]
    return []


def name_of(m: Model, x: str, expanded: set[str]) -> str:
    """The readable name drawn after the icons."""
    if address.is_own_code(x):
        return ""
    e = m.entities[x]
    if e.kind == "directory":
        return e.name if e.attrs.get("root") else display_path(m, x, expanded)
    if is_module(e):
        return module_stem(e.name)  # a source file is its module
    if e.kind == "image":
        return e.name.removesuffix("/Dockerfile")
    if e.kind == "endpoint" and "name" in e.attrs:
        return e.attrs["name"]  # the path below its step, often nothing: the verb says it all
    return e.name


def label_of(m: Model, x: str, expanded: set[str]) -> str:
    """The name in running text: the word-like icons stay in front ("svc api", "GET health")."""
    if m.has(x) and m.entities[x].kind == "endpoint":
        return m.entities[x].name
    icons = [i for i in icons_of(m, x) if i not in ("D", "m", "/", *SYMBOL_ICON.values())]
    name = name_of(m, x, expanded)
    return " ".join(icons + ([name] if name else []))


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
