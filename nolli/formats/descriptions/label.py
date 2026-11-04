"""``label``: how the picture names a drawn box. Zero or more small icons that say what it is
(``D`` directory, ``m`` module, the language's symbol icons (``C`` class, ``I`` interface,
``T`` type, ``E`` enum), ``svc``/``img`` deployment, ``API``, ``/`` a path step of an API, the
verbs of an operation), then a readable name (``text``); ``prose`` is the name in running
text, where the word-like icons stay in front ("svc api", "GET health")."""

from __future__ import annotations

from ...model import address
from ...model.scene import Element
from ...transformations.graph import Graph
from ...transformations.rollup import display_path

SYMBOL_ICON = {"class": "C", "interface": "I", "type": "T", "enum": "E", "function": "f"}  # model.SYMBOL_KINDS
VERB_ICON = {"WEBSOCKET": "WS", "DELETE": "DEL", "OPTIONS": "OPT"}


def is_module(g: Graph, x: str) -> bool:
    """A file of an extracted language (the file tree marks it): drawn ``m``, unfoldable."""
    return g.kind(x) == "file" and bool(g.attrs(x).get("module"))


def module_stem(file_name: str) -> str:
    """A source file's module name: without its extension, ``.d.ts`` included."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return stem[:-2] if stem.endswith(".d") and file_name.endswith(("ts", "mts", "cts")) else stem


def list_icons(g: Graph, x: str) -> list[str]:
    if address.is_own_code(x):
        return []
    kind = g.kind(x)
    if kind == "directory":
        return ["D"]
    if is_module(g, x):
        return ["m"]
    if kind in SYMBOL_ICON:
        return [SYMBOL_ICON[kind]]
    return {"service": ["svc"], "image": ["img"], "api": ["API"], "resource": ["/"]}.get(kind) or (
        [VERB_ICON.get(v, v) for v in g.attrs(x).get("method", "").split(",") if v] if kind == "endpoint" else [])


def format_name(g: Graph, x: str, expanded: set[str]) -> str:
    """The readable name drawn after the icons."""
    if address.is_own_code(x):
        return ""
    kind, a = g.kind(x), g.attrs(x)
    if kind == "directory":
        return g.name(x) if a.get("root") else display_path(g, x, expanded)
    if is_module(g, x):
        return module_stem(g.name(x))  # a source file is its module
    if kind == "image":
        return g.name(x).removesuffix("/Dockerfile")
    if kind == "endpoint" and "short" in a:
        return a["short"]  # the path below its step, often nothing: the verb says it all
    return g.name(x)


def format_prose(g: Graph, x: str, expanded: set[str]) -> str:
    """The name in running text: the word-like icons stay in front ("svc api", "GET health")."""
    if g.has(x) and g.kind(x) == "endpoint":
        return g.name(x)
    icons = [i for i in list_icons(g, x) if i not in ("D", "m", "/", *SYMBOL_ICON.values())]
    name = format_name(g, x, expanded)
    return " ".join(icons + ([name] if name else []))


def find_source(el: Element) -> tuple[Graph, set[str]] | None:
    """The source scene a drawn element was projected from (read as a Graph), and the cut."""
    src = el.scene.sources.get("source")
    if src is None:
        return None
    return src.memo(Graph), set(el.scene.model.meta.get("projection", {}).get("expanded", []))


class Label:
    name = "label"

    def compute(self, el: Element) -> dict | None:
        found = find_source(el)
        if found is None or el.is_edge:
            return None
        g, expanded = found
        if el.kind == "anchor":
            return {"text": "", "icons": [], "prose": ""}
        return {"text": format_name(g, el.key, expanded), "icons": list_icons(g, el.key), "prose": format_prose(g, el.key, expanded)}
