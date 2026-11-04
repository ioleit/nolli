"""How a node is called in the picture and in text, read from its address: a file's or
directory's name, a class's, a package's, a variable's. Where the address leaves something
out on purpose, from where it is known: the repository's name from the model's meta, a
namespace's title from the metamodel, an API's, a path step's and an endpoint's readable form
(the verb, the parameter names the code uses) from the extractor that made it."""

from __future__ import annotations

from urllib.parse import unquote

from ...model import address, metamodel
from ...model.scene import Element


def format_name(el: Element) -> str:
    x = el.key
    if address.is_root(x):
        return el.meta.get("repo") or "repository" if x == address.REPO_ROOT else metamodel.NAMESPACES[address.scheme_of(x)].title
    scheme, rest = x.split(":", 1)
    if scheme == address.REPO:
        fragment = address.fragment_of(x)
        return fragment[-1] if fragment else address.path_of(x).rsplit("/", 1)[-1]
    if scheme == address.API:
        for name in el.meta.get("overlays") or []:  # the extractor that made it says how it reads
            v = el.get(name)
            if v and v.get("name"):
                return v["name"]
        return rest
    if scheme == address.PKG:
        kind, _, name = rest.partition("/")
        return name if kind == "build" else unquote(name)
    if scheme == address.UNRESOLVED:
        return rest.split(":", 1)[-1]
    return unquote(rest)


def read_api(el: Element) -> dict:
    """What the extractor that made an API, a path step or an endpoint says about it: its
    protocol, method, path, the readable path below its step (``short``)."""
    if address.scheme_of(el.key) != address.API:
        return {}
    for name in el.meta.get("overlays") or []:
        v = el.get(name)
        if v and ("method" in v or "protocol" in v):
            return v
    return {}
