"""The ``names`` overlay, the readable name and icons of each node."""

from __future__ import annotations

from urllib.parse import unquote

from nolli.model import vocabulary
from nolli.model.scene import Element, Overlay
from nolli.model.schema import EntityId, Scheme

SYMBOL_ICON = {"class": "C", "interface": "I", "type": "T", "enum": "E", "function": "f"}  # schema.SYMBOL_KINDS
VERB_ICON = {"WEBSOCKET": "WS", "DELETE": "DEL", "OPTIONS": "OPT"}


class Names(Overlay):
    """The readable ``name`` and the ``icons`` of each node.

    The icons are the ones ``label.Label`` describes. A node's name and icons follow from its
    address and the extracted facts alone, so they are the same in every scene drawn from the
    model. They are computed when a model is built and kept with it."""

    name = "names"

    def compute(self, el: Element) -> dict | None:
        if el.is_edge:
            return None
        return {"name": _format_name(el), "icons": _list_icons(el)}


def is_module(el: Element) -> bool:
    """Whether the node is a source file in a language nolli extracts. Such a file is a
    module, drawn with ``m`` and unfoldable into its symbols."""
    return el.kind == "file" and bool(vocabulary.read_file(el).get("module"))


def _list_icons(el: Element) -> list[str]:
    if el.key.is_own_code:
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


def _format_name(el: Element) -> str:
    """Return the node's readable name, mostly the last part of its address, such as a file, class
    or package name. The repository's name comes from the model's meta, and root groups and
    APIs, whose addresses are not readable, take their names from the extractor that made them."""
    x = el.key
    if x.is_root:
        return el.meta.get("repo") or "repository" if x == EntityId.REPO_ROOT else vocabulary.read_external(el).get("name") or x
    scheme, rest = x.split(":", 1)
    if scheme == Scheme.REPO:
        fragment = x.fragment
        return fragment[-1] if fragment else x.path.rsplit("/", 1)[-1]
    if scheme == Scheme.API:
        return vocabulary.read_external(el).get("name") or rest
    if scheme == Scheme.PKG:
        kind, _, name = rest.partition("/")
        return name if kind == "build" else unquote(name)
    if scheme == Scheme.UNRESOLVED:
        return rest.split(":", 1)[-1]
    return unquote(rest)


def read_api(el: Element) -> dict:
    """Return the extractor's record of an API, path step or endpoint, with its ``protocol``,
    ``method``, ``path`` and ``short`` (the path below the parent step). It is empty for other nodes."""
    if el.key.scheme != Scheme.API:
        return {}
    v = vocabulary.read_external(el)
    return v if "method" in v or "protocol" in v else {}
