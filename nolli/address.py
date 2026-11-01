"""Where structural entities live: the model's namespaces and the addresses in them.

nolli is opinionated about structure: the model has a fixed set of namespaces, and an
extractor's job is to put what it finds into them. An entity's id is its address. Addresses
are made and read only here: every other module uses these functions, never string
operations on ids (``tests/test_address.py`` fails when another module spells a scheme).

An address names a thing within one snapshot. Base and head of a change produce the same
address for the same thing on their own, which is what lets a diff be one merged model
(``nolli/diff.py``). What is not identity stays out of the address: the kind and what
extractors say about a thing (a folder that becomes a package keeps its address), versions
(on edges), commits, provenance. Addresses are relative to the repository unless the thing
has a name of its own outside it (a package on a registry).

The namespaces, by scheme:

``repo:``
    A directory, a file or a part of a file in the repository, by its path relative to the
    repository root (``repo:nolli/scene.py``; the root itself is ``repo:``). A part of a
    file follows ``#``: segments joined by ``.``, which the file's extractor defines
    (``repo:nolli/scene.py#ViewState``, a nested class would be ``#Outer.Inner``).
``api:``
    An operation that code in scope serves or calls, and its groupings: the app serving it
    (``api:http:server/main.py:app``), a step of its path below the app, parameters written
    ``{}`` (``api:http:server/main.py:app/items/{}``), and the operation, its method after
    ``#`` (``api:http:server/main.py:app/items/{}#GET``).
``pkg:``
    An artifact: a built unit that is immutable and versioned, as a package URL
    (https://github.com/package-url/purl-spec): ``pkg:npm/elkjs``, ``pkg:npm/%40scope/name``.
    A Python import names a module, not the distribution on PyPI that provides it, so it is
    ``pkg:python/yaml``, not ``pkg:pypi/...``. An artifact built here that no registry names
    is ``pkg:build/`` and the path of its recipe (``pkg:build/web/Dockerfile``).
``runtime:``
    Something that executes and is reachable at runtime, by its host name: a compose
    service (``runtime:db``, which the other services reach as host ``db``), a host outside
    (``runtime:api.stripe.com``).
``config:``
    A name the code reads at runtime to steer its behaviour, wherever its value comes from
    (``config:DATABASE_URL``).

The root of each namespace (``api:``, ``pkg:``, ``runtime:``, ``config:``) is a group holding
its top-level entities; the root of ``repo:`` is the repository's directory. ``contain``
never crosses namespaces; which namespaces the other relations connect is ``RELATIONS``.

``unresolved:`` is not a namespace of the model's structure: it holds references an
extractor saw but could not bind to an address yet (``unresolved:py:requests.adapters``).
The matchers bind them (``nolli/match.py``); those left are gathered under its root.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

REPO, API, PKG, RUNTIME, CONFIG, UNRESOLVED = "repo", "api", "pkg", "runtime", "config", "unresolved"


@dataclass(frozen=True)
class Namespace:
    scheme: str
    title: str  # the name of its root group
    kinds: tuple[str, ...]  # what its entities may be (its root aside)
    structural: bool = True


NAMESPACES: dict[str, Namespace] = {ns.scheme: ns for ns in (
    Namespace(REPO, "Repository", ("directory", "file", "class", "interface", "type", "enum", "function")),  # model.SYMBOL_KINDS
    Namespace(API, "APIs", ("api", "resource", "endpoint")),
    Namespace(PKG, "Artifacts", ("package", "image")),
    Namespace(RUNTIME, "Runtime", ("service", "host")),
    Namespace(CONFIG, "Configuration", ("env",)),
    Namespace(UNRESOLVED, "Unresolved references", ("unresolved",), structural=False),
)}


# The other relations, by the namespaces of their ends (Model.add_relation checks them). A
# reference may end unresolved wherever it would end in the namespace it will be bound into.
RELATIONS: dict[str, set[tuple[str, str]]] = {
    "use": {(REPO, REPO), (REPO, API), (REPO, PKG), (REPO, CONFIG), (REPO, RUNTIME),  # imports, calls, reads, URLs
            (PKG, REPO),  # an image's entrypoint
            (RUNTIME, RUNTIME),  # a service depends on another or reaches it by URL
            (REPO, UNRESOLVED)},
    "provide": {(REPO, API),  # code serves an operation
                (RUNTIME, CONFIG)},  # a service sets a variable
    "allocate": {(REPO, PKG),  # files copied into an image
                 (PKG, RUNTIME)},  # an image run as a service
}


def check_relation(kind: str, src: str, dst: str) -> None:
    """Raises unless a ``kind`` relation may connect these two addresses."""
    ends = (scheme_of(src), scheme_of(dst))
    if kind == "contain":
        if ends[0] != ends[1]:
            raise ValueError(f"contain crosses namespaces: {src} -> {dst}")
    elif ends not in RELATIONS.get(kind, set()):
        raise ValueError(f"no {kind} from {ends[0]}: to {ends[1]}: ({src} -> {dst})")


def scheme_of(eid: str) -> str:
    """The namespace an address is in (``repo``)."""
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


def check(eid: str, kind: str) -> None:
    """Raises unless ``eid`` is an address whose namespace may hold an entity of ``kind``."""
    ns = NAMESPACES[scheme_of(eid)]
    if is_root(eid):
        if kind != ("directory" if ns.scheme == REPO else "group"):
            raise ValueError(f"the root of {ns.scheme}: cannot be a {kind}")
    elif kind not in ns.kinds:
        raise ValueError(f"{eid!r}: a {kind} has no place in {ns.scheme}: (holds {', '.join(ns.kinds)})")


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


def built_image(recipe: str) -> str:
    """An image built here from a Dockerfile at ``recipe`` (a repository path)."""
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
