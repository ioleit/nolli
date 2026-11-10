"""Names a drawn box with icons and a readable name."""

from __future__ import annotations

from collections.abc import Mapping

from nolli.formats.descriptions import names
from nolli.model import vocabulary
from nolli.model.model import Key
from nolli.model.scene import Element, Overlay, Scene


def _module_stem(file_name: str) -> str:
    """A source file's module name: without its extension, ``.d.ts`` included."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return stem[:-2] if stem.endswith(".d") and file_name.endswith(("ts", "mts", "cts")) else stem


def _format_path(el: Element, drawn: Element) -> str:
    """A directory's path below the box it is drawn in (a folded chain shows its whole run)."""
    path = el.key.path
    parent = drawn.parent
    if parent is not None and parent.key.is_repo and not parent.key.is_own_code:
        above = parent.key.path
        if above != "." and path.startswith(above + "/"):
            return path[len(above) + 1:]
    return path


def _format_label(el: Element, named: dict, drawn: Element | None = None) -> str:
    """The readable name drawn after the icons; ``named``: what the ``names`` overlay says of
    the node; ``drawn``: the box it is in a picture."""
    if el.key.is_own_code:
        return ""
    kind = el.kind
    if kind == "directory":
        return named["name"] if vocabulary.read_file(el).get("root") or drawn is None else _format_path(el, drawn)
    if names.is_module(el):
        return _module_stem(named["name"])  # a source file is its module
    if kind == "image":
        return named["name"].removesuffix("/Dockerfile")
    if kind == "endpoint" and "short" in names.read_api(el):
        return names.read_api(el)["short"]  # the path below its step, often nothing: the verb says it all
    return named["name"]


def format_prose(el: Element, named: dict, drawn: Element | None = None) -> str:
    """The name in running text: the word-like icons stay in front ("svc api", "GET health")."""
    if el.kind == "endpoint":
        return named["name"]
    icons = [i for i in named["icons"] if i not in ("D", "m", "/", *names.SYMBOL_ICON.values())]
    name = _format_label(el, named, drawn)
    return " ".join(icons + ([name] if name else []))


def _describe(s: Element, drawn: Element, named: dict) -> dict:
    """A drawn box named: its source node ``s``, what ``names`` says of it, and the box itself."""
    return {"text": _format_label(s, named, drawn), "icons": named["icons"], "prose": format_prose(s, named, drawn)}


class Label(Overlay):
    """The ``label`` overlay: how the picture names a drawn box. Zero or more small icons
    (``icons``) say what it is: ``D`` directory, ``m`` module, the language's symbol icons
    (``C`` class, ``I`` interface, ``T`` type, ``E`` enum), ``svc``/``img`` deployment, ``API``,
    ``/`` a path step of an API, the verbs of an operation. Then comes a readable name
    (``text``); ``prose`` is the name in running text, where the word-like icons stay in front
    ("svc api", "GET health")."""

    name = "label"

    def compute(self, el: Element) -> dict | None:
        return el.scene.read_all(self.name).get(el.key)  # named as a whole (compute_all)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """Every box of a picture named, from the names its source gives the nodes, read once."""
        if "source" not in scene.sources:
            return {}
        named = scene.sources["source"].read_all("names")
        out: dict[Key, dict] = {}
        for x, kind in ((n.id, n.kind) for n in scene.model.entities.values()):
            if kind == "anchor":
                out[x] = {"text": "", "icons": [], "prose": ""}
                continue
            el = scene.find_element(x)
            s = el.find_same("source")
            if s is not None and s.key in named:
                out[x] = _describe(s, el, named[s.key])
        return out
