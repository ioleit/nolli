"""What every language's overlays say in the same words: the shapes code that works with any
language may rely on (the roll-up counting references, the change overlay finding a class's
lines, the hover text). A language's overlay returns these, and may add what only it knows
(a subclass: ``nolli/extractors/typescript/vocabulary.py``). At run time they are plain dicts
(stored as maps, sent as JSON); ``check_map`` checks an extractor's map against them when
extraction is done.

Who says what: the file tree's overlay (``fs``) says what a file or directory is
(``FileFacts``); a language's symbols overlay what a module or a class defines
(``ModuleFacts``, ``SymbolFacts``), named in the model's ``meta["symbols"]`` by language; the
extractor that found an edge what references it stands for (``EdgeFacts``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    from .scene import Element


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
    """What the file tree says about a file or directory."""
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
    """What a language says a module defines outside its classes and types."""
    functions: list[str]
    constants: list[str]
    own_lines: int
    doc: NotRequired[str]
    reexports: NotRequired[list[str]]


class SymbolFacts(TypedDict):
    """A class, interface, type alias or enum."""
    path: str
    line: int
    end_line: int
    doc: NotRequired[str]
    exported: NotRequired[bool]


# -- reading them off an element (the model's ``meta`` names who says what) -----------------


def read_file(el: "Element") -> FileFacts | dict:
    """What the file tree says about a file or directory ({} for anything else)."""
    return el.get("fs") or {}


def read_references(el: "Element") -> list[Reference]:
    """The references an edge stands for, as the extractor that found it says ([] for none)."""
    for name in el.meta.get("overlays") or []:
        v = el.get(name)
        if v and "references" in v:
            return v["references"]
    return []


def count_references(el: "Element") -> int:
    return sum(r["count"] for r in read_references(el))


def read_symbols(el: "Element") -> ModuleFacts | SymbolFacts | dict:
    """What its language says a module, or a class (type, …) in one, defines ({} for anything else)."""
    f = el if el.kind == "file" else el.parent
    language = read_file(f).get("language") if f is not None else None
    name = (el.meta.get("symbols") or {}).get(language or "")
    return (el.get(name) or {}) if name else {}
