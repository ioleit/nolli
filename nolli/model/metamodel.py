"""What any model may contain: the kinds of entities and relations, the namespaces entities
live in, and which relations connect which namespaces.

A model is the graph of one codebase (a ``Model``, a dataset's ``model.json``); this module
is its metamodel. nolli is opinionated about structure: an extractor's job is to put what
it finds into these namespaces, and a new domain (data stores, queues) is a new namespace
here first. ``Model.add_entity`` and ``add_relation`` check every fact against it; how an
entity's id is spelled in its namespace is ``address.py``.

The vocabulary is the UML 2 component/deployment subset: entities are artifacts
(directory, file), components (service, image), interfaces (endpoint, env, host) or
external packages; relations are ``contain`` (ownership, a tree), ``use`` (Parnas's uses),
``provide`` (interface realization) and ``allocate`` (software element to environment
element: a directory copied into an image, an image run as a service).

The namespaces:

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
    Not structure: references an extractor saw but could not bind yet. The matchers bind
    them (``nolli/match.py``); those left are gathered under its root.

Each namespace has a root holding its top-level entities: a group, or for ``repo`` the
repository's directory. ``contain`` never crosses namespaces.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTAIN, USE, PROVIDE, ALLOCATE = "contain", "use", "provide", "allocate"
RELATION_KINDS = (CONTAIN, USE, PROVIDE, ALLOCATE)

# Entity kinds by UML metaclass. ``group`` is a namespace's root, a synthetic container with
# no counterpart in the repository.
ARTIFACT_KINDS = ("directory", "file")
SYMBOL_KINDS = ("class", "interface", "type", "enum", "function")  # module-viewtype elements inside a file
COMPONENT_KINDS = ("service", "image")
INTERFACE_KINDS = ("api", "resource", "endpoint", "env", "host")  # api > resource > endpoint: extractors/api.py
OTHER_KINDS = ("package", "group", "unresolved")
ENTITY_KINDS = ARTIFACT_KINDS + SYMBOL_KINDS + COMPONENT_KINDS + INTERFACE_KINDS + OTHER_KINDS

REPO, API, PKG, RUNTIME, CONFIG, UNRESOLVED = "repo", "api", "pkg", "runtime", "config", "unresolved"


@dataclass(frozen=True)
class Namespace:
    name: str  # also its scheme in addresses
    title: str  # the name of its root group
    kinds: tuple[str, ...]  # what its entities may be (its root aside)
    root_kind: str = "group"
    structural: bool = True


NAMESPACES: dict[str, Namespace] = {ns.name: ns for ns in (
    Namespace(REPO, "Repository", ARTIFACT_KINDS + SYMBOL_KINDS, root_kind="directory"),
    Namespace(API, "APIs", ("api", "resource", "endpoint")),
    Namespace(PKG, "Artifacts", ("package", "image")),
    Namespace(RUNTIME, "Runtime", ("service", "host")),
    Namespace(CONFIG, "Configuration", ("env",)),
    Namespace(UNRESOLVED, "Unresolved references", ("unresolved",), structural=False),
)}

# The other relations, by the namespaces of their ends. A reference may end unresolved
# wherever it would end in the namespace it will be bound into.
RELATIONS: dict[str, set[tuple[str, str]]] = {
    USE: {(REPO, REPO), (REPO, API), (REPO, PKG), (REPO, CONFIG), (REPO, RUNTIME),  # imports, calls, reads, URLs
          (PKG, REPO),  # an image's entrypoint
          (RUNTIME, RUNTIME),  # a service depends on another or reaches it by URL
          (REPO, UNRESOLVED)},
    PROVIDE: {(REPO, API),  # code serves an operation
              (RUNTIME, CONFIG)},  # a service sets a variable
    ALLOCATE: {(REPO, PKG),  # files copied into an image
               (PKG, RUNTIME)},  # an image run as a service
}


def check_entity(eid: str, namespace: str, root: bool, kind: str) -> None:
    """Raises unless ``namespace`` may hold an entity of ``kind`` (as its root if ``root``)."""
    if kind not in ENTITY_KINDS:
        raise ValueError(f"unknown entity kind {kind!r}")
    ns = NAMESPACES[namespace]
    if root:
        if kind != ns.root_kind:
            raise ValueError(f"the root of {namespace} cannot be a {kind}")
    elif kind not in ns.kinds:
        raise ValueError(f"{eid!r}: a {kind} has no place in {namespace} (holds {', '.join(ns.kinds)})")


def check_relation(kind: str, src: str, dst: str, src_namespace: str, dst_namespace: str) -> None:
    """Raises unless a ``kind`` relation may connect entities in these namespaces."""
    if kind not in RELATION_KINDS:
        raise ValueError(f"unknown relation kind {kind!r}")
    ends = (src_namespace, dst_namespace)
    if kind == CONTAIN:
        if ends[0] != ends[1]:
            raise ValueError(f"contain crosses namespaces: {src} -> {dst}")
    elif ends not in RELATIONS[kind]:
        raise ValueError(f"no {kind} from {ends[0]} to {ends[1]} ({src} -> {dst})")
