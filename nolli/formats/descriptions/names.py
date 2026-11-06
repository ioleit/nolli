"""Reads how a node is called from its address."""

from __future__ import annotations

from urllib.parse import unquote

from nolli.model import address, metamodel, vocabulary
from nolli.model.scene import Element


def format_name(el: Element) -> str:
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
