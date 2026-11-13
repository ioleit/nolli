"""The shapes of what overlays say about code, the same for every language.

A shape belongs here when code that works with any language relies on it; what only one
language knows is a key of its own in the value, read by that language's code alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, NotRequired, TypedDict

from nolli.model.model import Key
from nolli.model.schema import RelationId

if TYPE_CHECKING:
    from nolli.model.scene import Element, Scene


# The overlays that say these things, one per shape, whatever language's extractor said
# them: extraction files what each extractor said by shape (``extractors/facts.py``), so no
# reader needs to know which extractor spoke.
FILES = "files"  # the file tree: ``FileFacts`` of every file and directory
SYMBOLS = "symbols"  # what a module, class or type defines: ``ModuleFacts``, ``SymbolFacts``
REFERENCES = "references"  # ``EdgeFacts`` of every edge an extractor found references behind
EXTERNALS = "externals"  # a node outside the repository: how it is called, its language, an API's protocol and method
SHAPES = (FILES, SYMBOLS, REFERENCES, EXTERNALS)


def shape_of(key: Key, speaker: str) -> str:
    """Return the overlay a fact belongs in, given what it is about and who said it. An edge's
    facts go in references. A repository node's go in files when the file tree (``fs``) said
    them and in symbols when a language did. Facts about anything outside the repository go in
    externals."""
    if isinstance(key, RelationId):
        return REFERENCES
    if key.is_repo:
        return FILES if speaker == "fs" else SYMBOLS
    return EXTERNALS


# At run time these are plain dicts (kept as maps, sent as JSON); an extractor writes them
# and may add keys of its own.


class Reference(TypedDict):
    """One reference behind a ``use`` edge: code in ``path`` (at ``line``) names what the edge
    points to."""
    count: int  # references there: an import counts once, a name each use
    confidence: str  # exact | heuristic | statistical
    path: str
    line: NotRequired[int]  # not yet for references counted per name (the symbols extractors)
    name: NotRequired[str]  # the name used, where the language resolves names
    type_only: NotRequired[bool]  # only as a type: no dependency at run time (absent: False)


class EdgeFacts(TypedDict):
    """What an extractor says about an edge it found."""
    references: list[Reference]


class FileFacts(TypedDict):
    """What the file tree says about a file or directory (``FILES``)."""
    path: str
    role: str  # source, test, docs, config, data, asset, markup, other; a directory's also mixed
    language: NotRequired[str]  # an extracted language's files
    size: NotRequired[int]
    lines: NotRequired[int]
    module: NotRequired[bool]  # a file of an extracted language: a module
    body: NotRequired[str]  # its folder's own code (``__init__.py``, ``index.ts``): what it makes the folder ("package")
    root: NotRequired[bool]  # the repository's directory
    languages: NotRequired[dict]  # a directory: bytes per language


class ModuleFacts(TypedDict):
    """What a language says a module defines outside its classes and types (``SYMBOLS``)."""
    functions: list[str]
    constants: list[str]
    own_lines: int
    doc: NotRequired[str]
    reexports: NotRequired[list[str]]


class SymbolFacts(TypedDict):
    """What a language says about a class, interface, type alias or enum (``SYMBOLS``)."""
    path: str
    line: int
    end_line: int
    doc: NotRequired[str]
    exported: NotRequired[bool]


# -- reading them off an element or a whole scene ---------------------------------------


def read_file(el: Element) -> FileFacts | dict:
    """Return what the file tree says about a file or directory, or {} for any other element."""
    return el.get(FILES) or {}


def read_references(el: Element) -> list[Reference]:
    """Return the references an edge stands for, as the extractor that found it says, or [] for none."""
    return (el.get(REFERENCES) or {}).get("references", [])


def read_external(el: Element) -> dict:
    """Return what an extractor says about a node outside the repository: its ``name``, its
    ``language``, and for an API its ``method`` and ``protocol``. Return {} for a node inside the
    repository or one nothing was said about."""
    return el.get(EXTERNALS) or {}


def read_all_files(scene: Scene) -> Mapping[str, FileFacts]:
    """Return what the file tree says about every file and directory of a scene, by address."""
    return scene.read_all(FILES)  # type: ignore[return-value]


def read_all_references(scene: Scene) -> Mapping[RelationId, list[Reference]]:
    """Return the references every edge of a scene stands for, by edge, as ``read_references``
    reads them one by one. Edges no extractor found references for are left out."""
    return {k: v["references"] for k, v in scene.read_all(REFERENCES).items()}  # type: ignore[misc]


def read_all_symbols(scene: Scene) -> Mapping[str, ModuleFacts | SymbolFacts]:
    """Return what every module, class or type of a scene defines, by address."""
    return scene.read_all(SYMBOLS)  # type: ignore[return-value]

# -- the other well-known overlays --------------------------------------------------------
#
# This module is the right pattern for an overlay that code other than its producer relies
# on: one declaration of its name (a constant), of the shape of its value (a TypedDict) and
# of a reader (``read_*``), and consumers that import the reader rather than spell the name.
# We should use it for all well-known overlays. Today only extraction's four follow it; the
# others are a ``name = "..."`` on the class that produces them, spelled again as a string
# by each consumer (``viewer.SHOWN`` lists the viewer's once more), and the shape of their
# value is written down, if at all, only for the viewer, in TypeScript
# (``viewer/types/nolli.d.ts``). As of this writing:
#
# | Overlay      | Produced by                                  | Named as a string in                                   | Value (where known)                                    |
# |--------------|----------------------------------------------|--------------------------------------------------------|--------------------------------------------------------|
# | ``projection`` | ``projection.fold`` (a ``FixedOverlay``)     | merge, viewer, hover, mermaid                          | box: ``BoxProjection`` / ``AnchorProjection``; edge: ``EdgeProjection`` (nolli.d.ts) |
# | ``label``      | ``logic/formats/descriptions/label.py``      | viewer, mermaid, hover                                 | ``{text, icons, prose}`` (``Label``, nolli.d.ts)        |
# | ``names``      | ``logic/formats/descriptions/names.py``      | merge/reading, hover, label, review/card               | node: ``{name, icons}``                                 |
# | ``hover``      | ``logic/formats/descriptions/hover.py``      | viewer                                                 | ``{text}``, ``text`` an ``Info`` (nolli.d.ts)           |
# | ``change``     | ``merge/change.Change``                      | cli, merge/reading, merge/presets, projection/reading, viewer, mermaid, report, review/card, viewer's review panel (a table) | drawn: ``NodeDiff`` (``status``, ``totals``, ``own`` if the entity itself changed) / ``EdgeDiff``; on the change: a node's record, an edge's ``{status, base, head, fact}`` (``FactChange``) |
# | ``verdicts``   | ``review/verdicts.Verdicts``                 | viewer, review/card, viewer's review panel (a table)  | drawn: ``{verdicts, verdict, own, inside}`` (``Verdicts``, in part); on the change: ``{starting}`` (``ReviewVerdict``) |
# | ``review``     | ``review/card.Review``                       | viewer (``SHOWN``)                                     | ``{card}`` (``Review``)                                 |
# | ``content``    | a kept map (git's per file), no overlay class | operations                                             | a file: ``{git, added, removed, hunks}``, ``old_path`` when renamed (``logic/sources/git.py``) |
