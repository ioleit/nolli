"""The shapes of what overlays say about code, the same for every language.

A shape belongs here when code that works with any language relies on it; what only one
language knows is a key of its own in the value, read by that language's code alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, NotRequired, TypedDict

from nolli.model.model import Relation

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


def shape_of(key: str | Relation, speaker: str) -> str:
    """The overlay a fact belongs in, by what it is about and who said it: an edge's facts are
    references; a repository node's are files from the file tree (``fs``) and symbols from a
    language; anything outside the repository is externals."""
    if isinstance(key, Relation):
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
    """What the file tree says about a file or directory ({} for anything else)."""
    return el.get(FILES) or {}


def read_references(el: Element) -> list[Reference]:
    """The references an edge stands for, as the extractor that found it says ([] for none)."""
    return (el.get(REFERENCES) or {}).get("references", [])


def read_external(el: Element) -> dict:
    """What an extractor says about a node outside the repository ({} for one inside, or one
    nothing was said about): its ``name``, its ``language``, an API's ``method`` and ``protocol``."""
    return el.get(EXTERNALS) or {}


def read_all_files(scene: Scene) -> Mapping[str, FileFacts]:
    """What the file tree says about every file and directory of a scene, by address."""
    return scene.read_all(FILES)  # type: ignore[return-value]


def read_all_references(scene: Scene) -> Mapping[Relation, list[Reference]]:
    """The references every edge of a scene stands for, by edge (edges no extractor found
    references for are left out), as ``read_references`` reads them one by one."""
    return {k: v["references"] for k, v in scene.read_all(REFERENCES).items()}  # type: ignore[misc]


def read_all_symbols(scene: Scene) -> Mapping[str, ModuleFacts | SymbolFacts]:
    """What every module, class or type of a scene defines, by address."""
    return scene.read_all(SYMBOLS)  # type: ignore[return-value]