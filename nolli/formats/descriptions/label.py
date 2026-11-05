"""Names a drawn box with icons and a readable name."""

from __future__ import annotations

from nolli.formats.descriptions import names
from nolli.model import address, vocabulary
from nolli.model.scene import Element, Overlay

SYMBOL_ICON = {"class": "C", "interface": "I", "type": "T", "enum": "E", "function": "f"}  # metamodel.SYMBOL_KINDS
VERB_ICON = {"WEBSOCKET": "WS", "DELETE": "DEL", "OPTIONS": "OPT"}


def _is_module(el: Element) -> bool:
    """A file of an extracted language (the file tree marks it): drawn ``m``, unfoldable."""
    return el.kind == "file" and bool(vocabulary.read_file(el).get("module"))


def _module_stem(file_name: str) -> str:
    """A source file's module name: without its extension, ``.d.ts`` included."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return stem[:-2] if stem.endswith(".d") and file_name.endswith(("ts", "mts", "cts")) else stem


def _list_icons(el: Element) -> list[str]:
    if address.is_own_code(el.key):
        return []
    kind = el.kind
    if kind == "directory":
        return ["D"]
    if _is_module(el):
        return ["m"]
    if kind in SYMBOL_ICON:
        return [SYMBOL_ICON[kind]]
    return {"service": ["svc"], "image": ["img"], "api": ["API"], "resource": ["/"]}.get(kind) or (
        [VERB_ICON.get(v, v) for v in names.read_api(el).get("method", "").split(",") if v] if kind == "endpoint" else [])


def _format_path(el: Element, drawn: Element) -> str:
    """A directory's path below the box it is drawn in (a folded chain shows its whole run)."""
    path = address.path_of(el.key)
    parent = drawn.parent
    if parent is not None and address.is_repo(parent.key) and not address.is_own_code(parent.key):
        above = address.path_of(parent.key)
        if above != "." and path.startswith(above + "/"):
            return path[len(above) + 1:]
    return path


def _format_label(el: Element, drawn: Element | None = None) -> str:
    """The readable name drawn after the icons; ``drawn``: the box it is in a picture."""
    if address.is_own_code(el.key):
        return ""
    kind = el.kind
    if kind == "directory":
        return names.format_name(el) if vocabulary.read_file(el).get("root") or drawn is None else _format_path(el, drawn)
    if _is_module(el):
        return _module_stem(names.format_name(el))  # a source file is its module
    if kind == "image":
        return names.format_name(el).removesuffix("/Dockerfile")
    if kind == "endpoint" and "short" in names.read_api(el):
        return names.read_api(el)["short"]  # the path below its step, often nothing: the verb says it all
    return names.format_name(el)


def format_prose(el: Element, drawn: Element | None = None) -> str:
    """The name in running text: the word-like icons stay in front ("svc api", "GET health")."""
    if el.kind == "endpoint":
        return names.format_name(el)
    icons = [i for i in _list_icons(el) if i not in ("D", "m", "/", *SYMBOL_ICON.values())]
    name = _format_label(el, drawn)
    return " ".join(icons + ([name] if name else []))


class Label(Overlay):
    """The ``label`` overlay: how the picture names a drawn box. Zero or more small icons
    (``icons``) say what it is: ``D`` directory, ``m`` module, the language's symbol icons
    (``C`` class, ``I`` interface, ``T`` type, ``E`` enum), ``svc``/``img`` deployment, ``API``,
    ``/`` a path step of an API, the verbs of an operation. Then comes a readable name
    (``text``); ``prose`` is the name in running text, where the word-like icons stay in front
    ("svc api", "GET health")."""

    name = "label"

    def compute(self, el: Element) -> dict | None:
        if "source" not in el.scene.sources or el.is_edge:
            return None
        if el.kind == "anchor":
            return {"text": "", "icons": [], "prose": ""}
        s = el.find_same("source")
        if s is None:
            return None
        return {"text": _format_label(s, el), "icons": _list_icons(s), "prose": format_prose(s, el)}
