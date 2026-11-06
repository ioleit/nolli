"""Makes and reads addresses, the ids of entities in their namespaces.

Addresses are made and read only here; no other module works on ids with string operations.
An address names a thing within one snapshot of a repository, the same in every snapshot that
has it, so what is not its identity (its kind, what extractors say about it, versions,
commits, provenance) stays out of it.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from nolli.model import metamodel


def scheme_of(eid: str) -> str:
    """The namespace an address is in (``repo``), by its scheme: an address starts with its
    namespace's name and a colon."""
    scheme, sep, _ = eid.partition(":")
    if not sep or scheme not in metamodel.NAMESPACES:
        raise ValueError(f"not an address in a known namespace: {eid!r}")
    return scheme


def root(scheme: str) -> str:
    """A namespace's root: ``api:``. For ``repo:`` the repository's directory."""
    if scheme not in metamodel.NAMESPACES:
        raise ValueError(f"unknown namespace {scheme!r}")
    return scheme + ":"


REPO_ROOT = metamodel.REPO + ":"  # the repository's root directory


def is_root(eid: str) -> bool:
    return eid.endswith(":") and eid[:-1] in metamodel.NAMESPACES


# ---------- repo: ----------

def _segment(s: str) -> str:
    return quote(s, safe="").replace(".", "%2E")  # "." "#" "%" "/" and the like inside a name are escaped


def repo(path: str, *fragment: str) -> str:
    """A directory or file by its path relative to the repository root (``""`` or ``"."``
    for the root), or a part of a file by the segments its extractor gives it:
    ``repo:nolli/scene.py``, ``repo:`` for the root, ``repo:nolli/scene.py#ViewState``."""
    path = "" if path in ("", ".") else path
    return part(f"{metamodel.REPO}:{path}", *fragment)


def part(file: str, *fragment: str) -> str:
    """A part of the file ``file`` (a ``repo:`` address) by its segments, which the file's
    extractor defines: ``part(f, "Outer", "Inner")`` is ``…#Outer.Inner``. A ``.`` or ``#``
    inside a segment is escaped."""
    return file_of(file) + ("#" + ".".join(_segment(s) for s in fragment) if fragment else "")


def path_of(eid: str) -> str:
    """The repository path of a ``repo:`` address (``"."`` for the root), a part's file's."""
    if scheme_of(eid) != metamodel.REPO:
        raise ValueError(f"not in repo: {eid!r}")
    return eid[len(metamodel.REPO) + 1:].split("#", 1)[0] or "."


def fragment_of(eid: str) -> tuple[str, ...]:
    """The segments after ``#`` of a ``repo:`` address; ``()`` for a directory or file."""
    if not is_repo(eid) or "#" not in eid:
        return ()
    frag = eid.split("#", 1)[1]
    return tuple(unquote(s) for s in frag.split(".")) if frag else ()


def is_repo(eid: str) -> bool:
    return eid.startswith(metamodel.REPO + ":")


def is_part(eid: str) -> bool:
    """A part of a file (a class, …): an address with a fragment."""
    return is_repo(eid) and "#" in eid and not is_own_code(eid)


def file_of(eid: str) -> str:
    """The directory or file a ``repo:`` address is in or is: ``repo:a.py#X`` -> ``repo:a.py``."""
    return eid.split("#", 1)[0]


# A module's or package's own code (functions, constants, statements outside its classes; a
# package's __init__) is drawn as a node of its own but is no entity: the file's (or
# directory's) address with an empty fragment, ``repo:nolli/scene.py#``.
# TODO: revisit. It is not obvious what ``x`` is as opposed to ``x#``, and the own code may
# become a part of the file like any other, or no node at all.

def own_code(eid: str) -> str:
    """The node of a file's or directory's own code."""
    return file_of(eid) + "#"


def is_own_code(eid: str) -> bool:
    return eid.endswith("#") and is_repo(eid)


def format_identifier(eid: str) -> str:
    """The address as an identifier, as diagrams name a node (Mermaid's ``n_repo_nolli_scene_py``) and a
    review's verdicts name their targets."""
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", eid)


def owner(eid: str) -> str:
    """The entity a node stands for: the file or directory of an own-code node, else itself."""
    return eid[:-1] if is_own_code(eid) else eid


# ---------- api: ----------

def param(seg: str) -> str:
    """A path segment as the address has it: a parameter, written ``{name}`` by the adapters,
    is ``{}``, whatever the route calls it (``/items/{id}`` and ``/items/{item_id}`` are one)."""
    return "{}" if seg.startswith("{") and seg.endswith("}") else seg


def api_app(protocol: str, key: str) -> str:
    """An API: the app, server or service definition serving operations, ``key`` unique
    within the protocol: ``api:http:server/main.py:app``."""
    return f"{metamodel.API}:{protocol}:{key}"


def api_step(app: str, segments: list[str]) -> str:
    """A step of an API's path tree below ``app``, parameters written ``{}``:
    ``api:http:server/main.py:app/items/{}``."""
    return app + "".join("/" + param(s) for s in segments)


def api_operation(app: str, segments: list[str], method: str) -> str:
    """An operation: its method on a path below ``app``, after ``#``:
    ``api:http:server/main.py:app/items/{}#GET``."""
    return api_step(app, segments) + "#" + method


# ---------- pkg: ----------

PURL_TYPE = {"py": "python", "js": "npm"}  # language -> the package URL type its imports name


def package(lang: str, name: str) -> str:
    """A third-party package as a language's imports name it: ``("js", "@scope/name")``.

    The address is a package URL (https://github.com/package-url/purl-spec): ``pkg:npm/elkjs``,
    ``pkg:npm/%40scope/name``. It is not relative to the repository, since a package has a
    name of its own outside it. A Python import names a module, not the distribution on PyPI
    that provides it, so it is ``pkg:python/yaml``, not ``pkg:pypi/...``."""
    kind = PURL_TYPE[lang]
    if name.startswith("@") and "/" in name:
        scope, _, base = name.partition("/")
        return f"{metamodel.PKG}:{kind}/{quote(scope, safe='')}/{quote(base, safe='')}"
    return f"{metamodel.PKG}:{kind}/{quote(name, safe='')}"


def built(recipe: str) -> str:
    """An artifact built from the recipe at the repository path ``recipe`` that has no
    registry name: ``pkg:build/web/Dockerfile``. ``build`` is nolli's own package URL type,
    not a registered one."""
    return f"{metamodel.PKG}:build/{recipe}"


# ---------- runtime: and config: ----------

def runtime(host: str) -> str:
    """A compose service or a host outside, by the host name it is reached at: ``runtime:db``
    for a compose service the others reach as host ``db``, ``runtime:api.stripe.com``."""
    return f"{metamodel.RUNTIME}:{host}"


def config(name: str) -> str:
    """A setting the code reads at runtime, such as an environment variable, by its name:
    ``config:DATABASE_URL``."""
    return f"{metamodel.CONFIG}:{name}"


# ---------- unresolved: ----------

def unresolved(what: str, key: str) -> str:
    """A reference not bound yet: ``what`` says how to read ``key`` (``py`` a Python module,
    ``pyref`` a name in one, ``js`` an import specifier, ``tsref``, a protocol's call):
    ``unresolved:py:requests.adapters``."""
    return f"{metamodel.UNRESOLVED}:{what}:{key}"


def unresolved_key(eid: str, what: str) -> str | None:
    """The key of an unresolved reference of sort ``what``, None for any other address."""
    prefix = f"{metamodel.UNRESOLVED}:{what}:"
    return eid[len(prefix):] if eid.startswith(prefix) else None


def is_unresolved(eid: str) -> bool:
    return eid.startswith(metamodel.UNRESOLVED + ":")
