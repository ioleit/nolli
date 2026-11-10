"""The kinds of entities and relations a model may contain, the schemes its addresses start
with, and the addresses themselves (``EntityId``).

Addresses are made and read only here; no other module works on ids with string operations.
An address names a thing within one snapshot of a repository, the same in every snapshot that
has it, so what is not its identity (its kind, what extractors say about it, versions,
commits, provenance) stays out of it.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar
from urllib.parse import quote, unquote


class RelationKind(StrEnum):
    """The UML 2 component/deployment subset: ``contain`` (ownership, a tree), ``use``
    (Parnas's uses), ``provide`` (interface realization) and ``allocate`` (a software element
    to an environment element: a directory copied into an image, an image run as a service)."""

    CONTAIN = "contain"
    USE = "use"
    PROVIDE = "provide"
    ALLOCATE = "allocate"


class EntityKind(StrEnum):
    """Entity kinds by UML metaclass. ``group`` is a synthetic container with no counterpart
    in the repository (each scheme's root but ``repo:``); ``anchor`` is drawn only, in a
    projection (``nolli/logic/transformations/projection/__init__.py``)."""

    DIRECTORY = "directory"
    FILE = "file"
    CLASS = "class"
    INTERFACE = "interface"
    TYPE = "type"
    ENUM = "enum"
    FUNCTION = "function"
    SERVICE = "service"
    IMAGE = "image"
    API = "api"
    RESOURCE = "resource"
    ENDPOINT = "endpoint"
    ENV = "env"
    HOST = "host"
    PACKAGE = "package"
    GROUP = "group"
    UNRESOLVED = "unresolved"
    ANCHOR = "anchor"  # a drawn box for a node's own code


SYMBOL_KINDS = (EntityKind.CLASS, EntityKind.INTERFACE, EntityKind.TYPE, EntityKind.ENUM, EntityKind.FUNCTION)  # module-viewtype elements inside a file


class Scheme(StrEnum):
    """What an address starts with (``repo:…``), and the root that what has it hangs under:
    the repository's directory for ``repo``, a group for the others.

    ``repo``
        The repository: its directories and files, and the parts of files the language
        extractors find (classes, interfaces, types, enums).
    ``api``
        Operations that code in scope serves or calls, and their groupings: the app serving
        them, the steps of their paths.
    ``pkg``
        Artifacts: built units that are immutable and versioned, third-party packages and the
        images built in the repository.
    ``runtime``
        What executes and is reachable at runtime: compose services, hosts outside.
    ``config``
        Names the code reads at runtime to steer its behaviour, wherever their values come from.
    ``unresolved``
        References an extractor saw but could not bind yet. Those never bound are gathered
        under its root.
    """

    REPO = "repo"
    API = "api"
    PKG = "pkg"
    RUNTIME = "runtime"
    CONFIG = "config"
    UNRESOLVED = "unresolved"


SCHEMES = frozenset(Scheme)  # a plain str is looked up here as well

PURL_TYPE = {"py": "python", "js": "npm"}  # language -> the package URL type its imports name


def _segment(s: str) -> str:
    return quote(s, safe="").replace(".", "%2E")  # "." "#" "%" "/" and the like inside a name are escaped


def param(seg: str) -> str:
    """A path segment as the address has it: a parameter, written ``{name}`` by the adapters,
    is ``{}``, whatever the route calls it (``/items/{id}`` and ``/items/{item_id}`` are one)."""
    return "{}" if seg.startswith("{") and seg.endswith("}") else seg


class EntityId(str):
    """An address: ``<scheme>:<rest>``, a ``str`` that is checked to start with a known scheme
    when made. Make one with the ``from_*`` constructors (or ``EntityId(text)`` for one read
    back from JSON), take it apart with its properties.

    A ``str`` operation on it (``+``, slicing, ``split``) gives a plain ``str``: only the
    methods here give another ``EntityId``."""

    __slots__ = ()

    REPO_ROOT: ClassVar[EntityId]  # ``repo:``, the repository's root directory

    def __new__(cls, value: str) -> EntityId:
        if type(value) is cls:
            return value
        scheme, sep, _ = value.partition(":")
        if not sep or scheme not in SCHEMES:
            raise ValueError(f"not an address with a known scheme: {value!r}")
        return super().__new__(cls, value)

    # ---------- any scheme ----------

    @classmethod
    def root(cls, scheme: str) -> EntityId:
        """The root of a scheme's addresses: ``api:``. For ``repo:`` the repository's directory."""
        if scheme not in SCHEMES:
            raise ValueError(f"unknown scheme {scheme!r}")
        return cls(scheme + ":")

    @property
    def scheme(self) -> str:
        """What it starts with, before a colon (``repo``)."""
        return self.partition(":")[0]

    @property
    def is_root(self) -> bool:
        return self.endswith(":") and self[:-1] in SCHEMES

    def as_identifier(self) -> str:
        """The address as an identifier, as diagrams name a node (Mermaid's
        ``n_repo_nolli_scene_py``) and a review's verdicts name their targets."""
        return "n_" + re.sub(r"[^A-Za-z0-9]", "_", self)

    # ---------- repo: ----------

    @classmethod
    def from_path(cls, path: str, *fragment: str) -> EntityId:
        """A directory or file by its path relative to the repository root (``""`` or ``"."``
        for the root), or a part of a file by the segments its extractor gives it:
        ``repo:nolli/scene.py``, ``repo:`` for the root, ``repo:nolli/scene.py#ViewState``."""
        path = "" if path in ("", ".") else path
        return cls(f"{Scheme.REPO}:{path}").part(*fragment)

    def part(self, *fragment: str) -> EntityId:
        """A part of this file (a ``repo:`` address) by its segments, which the file's
        extractor defines: ``f.part("Outer", "Inner")`` is ``…#Outer.Inner``. A ``.`` or ``#``
        inside a segment is escaped."""
        return EntityId(self.file + ("#" + ".".join(_segment(s) for s in fragment) if fragment else ""))

    @property
    def is_repo(self) -> bool:
        return self.startswith(Scheme.REPO + ":")

    @property
    def path(self) -> str:
        """The repository path of a ``repo:`` address (``"."`` for the root), a part's file's."""
        if not self.is_repo:
            raise ValueError(f"not in repo: {self!r}")
        return self[len(Scheme.REPO) + 1:].split("#", 1)[0] or "."

    @property
    def fragment(self) -> tuple[str, ...]:
        """The segments after ``#`` of a ``repo:`` address; ``()`` for a directory or file."""
        if not self.is_repo or "#" not in self:
            return ()
        frag = self.split("#", 1)[1]
        return tuple(unquote(s) for s in frag.split(".")) if frag else ()

    @property
    def is_part(self) -> bool:
        """A part of a file (a class, …): an address with a fragment."""
        return self.is_repo and "#" in self and not self.is_own_code

    @property
    def file(self) -> EntityId:
        """The directory or file a ``repo:`` address is in or is: ``repo:a.py#X`` -> ``repo:a.py``."""
        return EntityId(self.split("#", 1)[0])

    # A module's or package's own code (functions, constants, statements outside its classes; a
    # package's __init__) is drawn as a node of its own but is no entity: the file's (or
    # directory's) address with an empty fragment, ``repo:nolli/scene.py#``.
    # TODO: revisit. It is not obvious what ``x`` is as opposed to ``x#``, and the own code may
    # become a part of the file like any other, or no node at all.

    def own_code(self) -> EntityId:
        """The node of a file's or directory's own code."""
        return EntityId(self.file + "#")

    @property
    def is_own_code(self) -> bool:
        return self.endswith("#") and self.is_repo

    @property
    def owner(self) -> EntityId:
        """The entity a node stands for: the file or directory of an own-code node, else itself."""
        return EntityId(self[:-1]) if self.is_own_code else self

    # ---------- api: ----------

    @classmethod
    def from_api_app(cls, protocol: str, key: str) -> EntityId:
        """An API: the app, server or service definition serving operations, ``key`` unique
        within the protocol: ``api:http:server/main.py:app``."""
        return cls(f"{Scheme.API}:{protocol}:{key}")

    def api_step(self, segments: list[str]) -> EntityId:
        """A step of this API's path tree, parameters written ``{}``:
        ``api:http:server/main.py:app/items/{}``."""
        return EntityId(self + "".join("/" + param(s) for s in segments))

    def api_operation(self, segments: list[str], method: str) -> EntityId:
        """An operation of this API: its method on a path below it, after ``#``:
        ``api:http:server/main.py:app/items/{}#GET``."""
        return EntityId(self.api_step(segments) + "#" + method)

    # ---------- pkg: ----------

    @classmethod
    def from_package(cls, lang: str, name: str) -> EntityId:
        """A third-party package as a language's imports name it: ``("js", "@scope/name")``.

        The address is a package URL (https://github.com/package-url/purl-spec): ``pkg:npm/elkjs``,
        ``pkg:npm/%40scope/name``. It is not relative to the repository, since a package has a
        name of its own outside it. A Python import names a module, not the distribution on PyPI
        that provides it, so it is ``pkg:python/yaml``, not ``pkg:pypi/...``."""
        kind = PURL_TYPE[lang]
        if name.startswith("@") and "/" in name:
            scope, _, base = name.partition("/")
            return cls(f"{Scheme.PKG}:{kind}/{quote(scope, safe='')}/{quote(base, safe='')}")
        return cls(f"{Scheme.PKG}:{kind}/{quote(name, safe='')}")

    @classmethod
    def from_build_recipe(cls, recipe: str) -> EntityId:
        """An artifact built from the recipe at the repository path ``recipe`` that has no
        registry name: ``pkg:build/web/Dockerfile``. ``build`` is nolli's own package URL type,
        not a registered one."""
        return cls(f"{Scheme.PKG}:build/{recipe}")

    # ---------- runtime: and config: ----------

    @classmethod
    def from_host(cls, host: str) -> EntityId:
        """A compose service or a host outside, by the host name it is reached at: ``runtime:db``
        for a compose service the others reach as host ``db``, ``runtime:api.stripe.com``."""
        return cls(f"{Scheme.RUNTIME}:{host}")

    @classmethod
    def from_config(cls, name: str) -> EntityId:
        """A setting the code reads at runtime, such as an environment variable, by its name:
        ``config:DATABASE_URL``."""
        return cls(f"{Scheme.CONFIG}:{name}")

    # ---------- unresolved: ----------

    @classmethod
    def from_unresolved(cls, what: str, key: str) -> EntityId:
        """A reference not bound yet: ``what`` says how to read ``key`` (``py`` a Python module,
        ``pyref`` a name in one, ``js`` an import specifier, ``tsref``, a protocol's call):
        ``unresolved:py:requests.adapters``."""
        return cls(f"{Scheme.UNRESOLVED}:{what}:{key}")

    def unresolved_key(self, what: str) -> str | None:
        """The key of an unresolved reference of sort ``what``, None for any other address."""
        prefix = f"{Scheme.UNRESOLVED}:{what}:"
        return self[len(prefix):] if self.startswith(prefix) else None

    @property
    def is_unresolved(self) -> bool:
        return self.startswith(Scheme.UNRESOLVED + ":")


EntityId.REPO_ROOT = EntityId.root(Scheme.REPO)
