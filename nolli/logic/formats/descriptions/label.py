"""The ``label`` overlay, the icons and name shown on each box of a drawn scene."""

from __future__ import annotations

from collections.abc import Mapping

from nolli.logic.formats.descriptions import names
from nolli.model import schema
from nolli.model.model import Key
from nolli.model.scene import Element, Overlay, Scene


def _module_stem(file_name: str) -> str:
    """Return the file name without its extension, so ``a.py`` and ``a.d.ts`` both give ``a``."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return stem[:-2] if stem.endswith(".d") and file_name.endswith(("ts", "mts", "cts")) else stem


def _format_label(el: Element, named: dict) -> str:
    """Return the name shown after the icons. ``named`` is the node's entry in the ``names``
    overlay."""
    if el.key.is_own_code:
        return ""
    kind = el.kind
    if names.is_module(el):
        return _module_stem(named["name"])  # a module is named by its file
    if kind == "image":
        return named["name"].removesuffix("/Dockerfile")
    if kind == "endpoint" and "short" in names.read_api(el):
        return names.read_api(el)["short"]  # path below the parent step, often empty, leaving just the verb
    return named["name"]


def format_prose(el: Element, named: dict) -> str:
    """Return the name as used in a sentence. Icons that read as words stay in front of it, as in
    "svc api" or "GET health"."""
    if el.kind == "endpoint":
        return named["name"]
    icons = [i for i in named["icons"] if i not in ("D", "m", "/", *names.SYMBOL_ICON.values())]
    name = _format_label(el, named)
    return " ".join(icons + ([name] if name else []))


def _format_path(s: Element, named: dict) -> str | None:
    """Return where in the repository the node is, as a list of changes names it: a file's or
    directory's path, a class (or other symbol) as path#Name; None outside the file tree."""
    if not s.key.is_repo or s.key.is_own_code:
        return None
    return f"{s.key.path}#{named['name']}" if s.kind in schema.SYMBOL_KINDS else s.key.path


def _describe(s: Element, named: dict) -> dict:
    """Build the label of a box that stands for the node ``s``."""
    return {"text": _format_label(s, named), "icons": named["icons"], "prose": format_prose(s, named), "path": _format_path(s, named)}


class Label(Overlay):
    """The label of each box in a drawn scene, made of ``icons``, ``text`` and ``prose``.

    The icons show what kind of thing the box is. ``D`` is a directory, ``m`` a module,
    ``C``, ``I``, ``T`` and ``E`` a class, interface, type and enum, ``svc`` and ``img`` a
    service and an image, ``API`` an API, ``/`` a step in an API path, and HTTP verbs an
    operation. ``text`` is the name shown after the icons, ``prose`` is the name as used in a
    sentence, and ``path`` where in the repository it is (None outside the file tree)."""

    name = "label"

    def compute(self, el: Element) -> dict | None:
        return el.scene.read_all(self.name).get(el.key)  # computed for the whole scene in compute_all

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """Build the labels of all boxes from the ``names`` overlay of the scene this one was
        drawn from. A scene not drawn from another has none."""
        if "source" not in scene.sources:
            return {}
        named = scene.sources["source"].read_all("names")
        out: dict[Key, dict] = {}
        for x, kind in ((n.id, n.kind) for n in scene.model.entities.values()):
            if kind == "anchor":
                out[x] = {"text": "", "icons": [], "prose": "", "path": None}  # an anchor stands inside its node's box and has no label of its own
                continue
            el = scene.find_element(x)
            s = el.find_same("source")
            if s is not None and s.key in named:
                out[x] = _describe(s, named[s.key])
        return out
