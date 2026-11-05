"""The shapes of what overlays say about code, the same for every language.

A shape belongs here when code that works with any language relies on it; what only one
language knows belongs in that language's own subclass of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, NotRequired, TypedDict

from nolli.model import EdgeKey

if TYPE_CHECKING:
    from nolli.model.scene import Element, Scene


# At run time these are plain dicts (stored as maps, sent as JSON); a language's overlay
# returns them and may add keys of its own.


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
    """What the file tree's overlay (``fs``) says about a file or directory."""
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
    """What a language's symbols overlay says a module defines outside its classes and types."""
    functions: list[str]
    constants: list[str]
    own_lines: int
    doc: NotRequired[str]
    reexports: NotRequired[list[str]]


class SymbolFacts(TypedDict):
    """What a language's symbols overlay says about a class, interface, type alias or enum."""
    path: str
    line: int
    end_line: int
    doc: NotRequired[str]
    exported: NotRequired[bool]


# -- reading them off an element (the model's ``meta`` names who says what) -----------------


def read_file(el: Element) -> FileFacts | dict:
    """What the file tree says about a file or directory ({} for anything else)."""
    return el.get("fs") or {}


def read_references(el: Element) -> list[Reference]:
    """The references an edge stands for, as the extractor that found it says ([] for none)."""
    for name in el.meta.get("overlays") or []:
        v = el.get(name)
        if v and "references" in v:
            return v["references"]
    return []


def read_all_files(scene: Scene) -> Mapping[str, FileFacts]:
    """What the file tree says about every file and directory of a scene, by address."""
    return scene.read_all("fs")  # type: ignore[return-value]


def read_all_references(scene: Scene) -> Mapping[EdgeKey, list[Reference]]:
    """The references every edge of a scene stands for, by edge (edges no extractor found
    references for are left out), as ``read_references`` reads them one by one; merged once
    per scene from the extractors' maps."""
    return scene.memo(_merge_references)


def _merge_references(scene: Scene) -> dict[EdgeKey, list[Reference]]:
    out: dict[EdgeKey, list[Reference]] = {}
    for name in scene.model.meta.get("overlays") or []:
        for k, v in scene.read_all(name).items():
            if isinstance(k, EdgeKey) and "references" in v and k not in out:
                out[k] = v["references"]
    return out


def read_all_symbols(scene: Scene) -> dict[str, ModuleFacts | SymbolFacts]:
    """What every module, class or type of a scene defines, by address, each from the symbols
    overlay of its language."""
    files = read_all_files(scene)
    model = scene.model
    out: dict[str, ModuleFacts | SymbolFacts] = {}
    for language, name in (model.meta.get("symbols") or {}).items():
        for k, v in scene.read_all(name).items():
            if isinstance(k, EdgeKey) or not model.has(k):
                continue
            f = k if model.find_kind(k) == "file" else model.find_parent(k)
            if f is not None and files.get(f, {}).get("language") == language:
                out[k] = v  # type: ignore[assignment]
    return out