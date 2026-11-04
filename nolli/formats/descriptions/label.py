"""``label``: how the picture names a drawn box. Zero or more small icons that say what it is
(``D`` directory, ``m`` module, the language's symbol icons (``C`` class, ``I`` interface,
``T`` type, ``E`` enum), ``svc``/``img`` deployment, ``API``, ``/`` a path step of an API, the
verbs of an operation), then a readable name (``text``); ``prose`` is the name in running
text, where the word-like icons stay in front ("svc api", "GET health")."""

from __future__ import annotations

from nolli.model import address
from nolli.model.scene import Element
from nolli.model.vocabulary import read_file

from .names import format_name, read_api

SYMBOL_ICON = {"class": "C", "interface": "I", "type": "T", "enum": "E", "function": "f"}  # model.SYMBOL_KINDS
VERB_ICON = {"WEBSOCKET": "WS", "DELETE": "DEL", "OPTIONS": "OPT"}


def is_module(el: Element) -> bool:
    """A file of an extracted language (the file tree marks it): drawn ``m``, unfoldable."""
    return el.kind == "file" and bool(read_file(el).get("module"))


def module_stem(file_name: str) -> str:
    """A source file's module name: without its extension, ``.d.ts`` included."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return stem[:-2] if stem.endswith(".d") and file_name.endswith(("ts", "mts", "cts")) else stem


def list_icons(el: Element) -> list[str]:
    if address.is_own_code(el.key):
        return []
    kind = el.kind
    if kind == "directory":
        return ["D"]
    if is_module(el):
        return ["m"]
    if kind in SYMBOL_ICON:
        return [SYMBOL_ICON[kind]]
    return {"service": ["svc"], "image": ["img"], "api": ["API"], "resource": ["/"]}.get(kind) or (
        [VERB_ICON.get(v, v) for v in read_api(el).get("method", "").split(",") if v] if kind == "endpoint" else [])


def format_path(el: Element, drawn: Element) -> str:
    """A directory's path below the box it is drawn in (a folded chain shows its whole run)."""
    path = address.path_of(el.key)
    parent = drawn.parent
    if parent is not None and address.is_repo(parent.key) and not address.is_own_code(parent.key):
        above = address.path_of(parent.key)
        if above != "." and path.startswith(above + "/"):
            return path[len(above) + 1:]
    return path


def format_label(el: Element, drawn: Element | None = None) -> str:
    """The readable name drawn after the icons; ``drawn``: the box it is in a picture."""
    if address.is_own_code(el.key):
        return ""
    kind = el.kind
    if kind == "directory":
        return format_name(el) if read_file(el).get("root") or drawn is None else format_path(el, drawn)
    if is_module(el):
        return module_stem(format_name(el))  # a source file is its module
    if kind == "image":
        return format_name(el).removesuffix("/Dockerfile")
    if kind == "endpoint" and "short" in read_api(el):
        return read_api(el)["short"]  # the path below its step, often nothing: the verb says it all
    return format_name(el)


def format_prose(el: Element, drawn: Element | None = None) -> str:
    """The name in running text: the word-like icons stay in front ("svc api", "GET health")."""
    if el.kind == "endpoint":
        return format_name(el)
    icons = [i for i in list_icons(el) if i not in ("D", "m", "/", *SYMBOL_ICON.values())]
    name = format_label(el, drawn)
    return " ".join(icons + ([name] if name else []))


class Label:
    name = "label"

    def compute(self, el: Element) -> dict | None:
        if "source" not in el.scene.sources or el.is_edge:
            return None
        if el.kind == "anchor":
            return {"text": "", "icons": [], "prose": ""}
        s = el.find_same("source")
        if s is None:
            return None
        return {"text": format_label(s, el), "icons": list_icons(s), "prose": format_prose(s, el)}
