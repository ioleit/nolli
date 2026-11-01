"""How an entity's id is spelled: its address in its namespace (``metamodel.py``).

An entity's id is its address. Addresses are made and read only here: every other module
uses these functions, never string operations on ids (``tests/test_address.py`` fails when
another module spells a scheme).

An address names a thing within one snapshot. Base and head of a change produce the same
address for the same thing on their own, which is what lets a diff be one merged model
(``nolli/diff.py``). What is not identity stays out of the address: the kind and what
extractors say about a thing (a folder that becomes a package keeps its address), versions
(on edges), commits, provenance. Addresses are relative to the repository unless the thing
has a name of its own outside it (a package on a registry).

An address is the namespace's name as its scheme, then:

``repo:``
    The path relative to the repository root (``repo:nolli/scene.py``; the root itself is
    ``repo:``). A part of a file follows ``#``: segments joined by ``.``, which the file's
    extractor defines (``repo:nolli/scene.py#ViewState``, a nested class would be
    ``#Outer.Inner``).
``api:``
    The protocol and the app (``api:http:server/main.py:app``), a step of the path below the
    app, parameters written ``{}`` (``api:http:server/main.py:app/items/{}``), and for an
    operation its method after ``#`` (``api:http:server/main.py:app/items/{}#GET``).
``pkg:``
    A package URL (https://github.com/package-url/purl-spec): ``pkg:npm/elkjs``,
    ``pkg:npm/%40scope/name``. A Python import names a module, not the distribution on PyPI
    that provides it, so it is ``pkg:python/yaml``, not ``pkg:pypi/...``. An artifact built
    in the repository that no registry names is ``pkg:build/`` and the repository path of
    the recipe it is built from (``pkg:build/web/Dockerfile``); ``build`` is nolli's own
    type, not a registered one.
``runtime:``
    The host name it is reached at: ``runtime:db`` for a compose service (the other services
    reach it as host ``db``), ``runtime:api.stripe.com``.
``config:``
    The name (``config:DATABASE_URL``).
``unresolved:``
    What the reference names, after the sort of reference (``unresolved:py:requests.adapters``).

A namespace's root is its scheme alone (``api:``, ``repo:``).
"""

from __future__ import annotations

from urllib.parse import quote, unquote

from .metamodel import API, CONFIG, NAMESPACES, PKG, REPO, RUNTIME, UNRESOLVED  # noqa: F401 (spelled through address)


def scheme_of(eid: str) -> str:
    """The namespace an address is in (``repo``), by its scheme."""
    scheme, sep, _ = eid.partition(":")
    if not sep or scheme not in NAMESPACES:
        raise ValueError(f"not an address in a known namespace: {eid!r}")
    return scheme


def root(scheme: str) -> str:
    """A namespace's root: ``api:``. For ``repo:`` the repository's directory."""
    if scheme not in NAMESPACES:
        raise ValueError(f"unknown namespace {scheme!r}")
    return scheme + ":"


REPO_ROOT = REPO + ":"  # the repository's root directory


def is_root(eid: str) -> bool:
    return eid.endswith(":") and eid[:-1] in NAMESPACES


# ---------- repo: ----------

def _segment(s: str) -> str:
    return quote(s, safe="").replace(".", "%2E")  # "." "#" "%" "/" and the like inside a name are escaped


def repo(path: str, *fragment: str) -> str:
    """A directory or file by its path relative to the repository root (``""`` or ``"."``
    for the root), or a part of a file by the segments its extractor gives it."""
    path = "" if path in ("", ".") else path
    return part(f"{REPO}:{path}", *fragment)


def part(file: str, *fragment: str) -> str:
    """A part of the file ``file`` (a ``repo:`` address) by its segments: ``part(f, "Outer", "Inner")``."""
    return file_of(file) + ("#" + ".".join(_segment(s) for s in fragment) if fragment else "")


def path_of(eid: str) -> str:
    """The repository path of a ``repo:`` address (``"."`` for the root), a part's file's."""
    if scheme_of(eid) != REPO:
        raise ValueError(f"not in repo: {eid!r}")
    return eid[len(REPO) + 1:].split("#", 1)[0] or "."


def fragment_of(eid: str) -> tuple[str, ...]:
    """The segments after ``#`` of a ``repo:`` address; ``()`` for a directory or file."""
    if not is_repo(eid) or "#" not in eid:
        return ()
    frag = eid.split("#", 1)[1]
    return tuple(unquote(s) for s in frag.split(".")) if frag else ()


def is_repo(eid: str) -> bool:
    return eid.startswith(REPO + ":")


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
    within the protocol (``server/main.py:app``)."""
    return f"{API}:{protocol}:{key}"


def api_step(app: str, segments: list[str]) -> str:
    """A step of an API's path tree below ``app``."""
    return app + "".join("/" + param(s) for s in segments)


def api_operation(app: str, segments: list[str], method: str) -> str:
    """An operation: its method on a path below ``app``."""
    return api_step(app, segments) + "#" + method


# ---------- pkg: ----------

PURL_TYPE = {"py": "python", "js": "npm"}  # language -> the package URL type its imports name


def package(lang: str, name: str) -> str:
    """A third-party package as a language's imports name it: ``("js", "@scope/name")``."""
    kind = PURL_TYPE[lang]
    if name.startswith("@") and "/" in name:
        scope, _, base = name.partition("/")
        return f"{PKG}:{kind}/{quote(scope, safe='')}/{quote(base, safe='')}"
    return f"{PKG}:{kind}/{quote(name, safe='')}"


def built(recipe: str) -> str:
    """An artifact built from the recipe at the repository path ``recipe`` that has no
    registry name."""
    return f"{PKG}:build/{recipe}"


# ---------- runtime: and config: ----------

def runtime(host: str) -> str:
    """A compose service or a host outside, by the host name it is reached at."""
    return f"{RUNTIME}:{host}"


def config(name: str) -> str:
    """A setting the code reads at runtime, such as an environment variable."""
    return f"{CONFIG}:{name}"


# ---------- unresolved: ----------

def unresolved(what: str, key: str) -> str:
    """A reference not bound yet: ``what`` says how to read ``key`` (``py`` a Python module,
    ``pyref`` a name in one, ``js`` an import specifier, ``tsref``, a protocol's call)."""
    return f"{UNRESOLVED}:{what}:{key}"


def unresolved_key(eid: str, what: str) -> str | None:
    """The key of an unresolved reference of sort ``what``, None for any other address."""
    prefix = f"{UNRESOLVED}:{what}:"
    return eid[len(prefix):] if eid.startswith(prefix) else None


def is_unresolved(eid: str) -> bool:
    return eid.startswith(UNRESOLVED + ":")
