"""How a node is called: the ``names`` overlay, read from its address and what extraction said."""

from __future__ import annotations

from urllib.parse import unquote

from nolli.model import address, metamodel, vocabulary
from nolli.model.scene import Element, Overlay

SYMBOL_ICON = {"class": "C", "interface": "I", "type": "T", "enum": "E", "function": "f"}  # metamodel.SYMBOL_KINDS
VERB_ICON = {"WEBSOCKET": "WS", "DELETE": "DEL", "OPTIONS": "OPT"}


class Names(Overlay):
    """The ``names`` overlay: how a node is called, the same in every picture of its scene. Its
    readable ``name`` (``_format_name``) and the ``icons`` that say what it is (``_list_icons``:
    ``D`` directory, ``m`` module, ``C`` class and the other symbols, ``svc``/``img``, ``API``,
    ``/`` a path step, the verbs of an endpoint). A fact about an address: kept with a model at
    build time and read back from there."""

    name = "names"

    def compute(self, el: Element) -> dict | None:
        if el.is_edge:
            return None
        return {"name": _format_name(el), "icons": _list_icons(el)}


def is_module(el: Element) -> bool:
    """A file of an extracted language (the file tree marks it): drawn ``m``, unfoldable."""
    return el.kind == "file" and bool(vocabulary.read_file(el).get("module"))


def _list_icons(el: Element) -> list[str]:
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


def _format_name(el: Element) -> str:
    """How a node is called in the picture and in text, read from its address: a file's or
    directory's name, a class's, a package's, a variable's. Where the address leaves something
    out on purpose, from where it is known: the repository's name from the model's meta, a
    namespace's title from the metamodel, an API's readable form from the extractor that made
    it (see ``read_api``)."""
    x = el.key
    if address.is_root(x):
        return el.meta.get("repo") or "repository" if x == address.REPO_ROOT else metamodel.NAMESPACES[address.scheme_of(x)].title
    scheme, rest = x.split(":", 1)
    if scheme == metamodel.REPO:
        fragment = address.fragment_of(x)
        return fragment[-1] if fragment else address.path_of(x).rsplit("/", 1)[-1]
    if scheme == metamodel.API:
        return vocabulary.read_external(el).get("name") or rest  # the extractor that made it says how it reads
    if scheme == metamodel.PKG:
        kind, _, name = rest.partition("/")
        return name if kind == "build" else unquote(name)
    if scheme == metamodel.UNRESOLVED:
        return rest.split(":", 1)[-1]
    return unquote(rest)


def read_api(el: Element) -> dict:
    """What the extractor that made an API, a path step or an endpoint says about it: its
    protocol, method (the verb), path (with the parameter names the code uses), the readable
    path below its step (``short``)."""
    if address.scheme_of(el.key) != metamodel.API:
        return {}
    v = vocabulary.read_external(el)
    return v if "method" in v or "protocol" in v else {}
