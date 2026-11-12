"""Merges the models of two commits into the model of their change.

It also says what the change did: per node and edge in the ``change`` overlay, and as a
whole in the change's totals and its links on GitHub. ``items`` numbers the changes and says
which box and edge shows each (the ``items`` overlay); ``reading`` reads a change's scene for it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

from nolli.model import schema, vocabulary
from nolli.model.keys import ModelKey
from nolli.model.model import Key, Model, Relation
from nolli.model.scene import Element, Overlay, Scene, StoredMaps
from nolli.model.schema import EntityId, RelationId, RelationKind


def is_change(scene: Scene) -> bool:
    return "base" in scene.sources


class Change(Overlay):
    """The ``change`` overlay. It says what a change did to each node and edge.

    On a change's own nodes and edges it compares the two sides: a node or edge on one side
    only is ``added`` or ``removed``, an edge whose number of references differs is
    ``changed``. What changed inside a file is git's word, kept with the change as its
    ``content`` map: a file gets git's status and its added and removed lines, a class the
    changed lines inside its line range. On a box or edge of a scene drawn from a change
    (role ``source``) it sums up what changed in what that box or edge stands for."""

    name = "change"

    def __init__(self, content: StoredMaps) -> None:
        self.content = content

    def compute(self, el: Element) -> dict | None:
        if is_change(el.scene):  # computed for the whole change in compute_all
            return el.scene.read_all(self.name).get(el.key)
        if "source" in el.scene.sources:  # a drawn scene sums up the changes of what each element stands for
            return _sum_edge(el) if el.is_edge else _sum_box(el)
        return None

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """For a change, each node and edge compared between the maps of its two sides. A
        scene drawn from a change sums those up element by element, and a scene of a single
        commit has no changes."""
        if not is_change(scene):
            return Overlay.compute_all(self, scene) if "change" in scene.model.meta else {}
        out: dict[Key, dict] = {}
        sides = {role: _Side(scene.sources[role]) for role in ("base", "head")}
        content = self.content.load(scene.model.key.format_key())
        renames, by_old_path = _list_renames(content), _index_old_paths(content)
        for x in scene.model.entities.values():
            rec = _compare_node(x.id, x.kind, sides, content, renames, by_old_path)
            if rec is not None:
                out[x.id] = rec
        for e in scene.model.edges:
            rec = _compare_edge(e, scene.model.meta, {role: s.model.has(e.id) and s.references.get(e.id, []) for role, s in sides.items()})
            if rec is not None:
                out[e.id] = rec
        return out


class _Side:
    """One side of a change as the comparison reads it: its model and its maps, all at once."""

    def __init__(self, scene: Scene) -> None:
        self.model = scene.model
        self.references = vocabulary.read_all_references(scene)
        self.symbols = vocabulary.read_all_symbols(scene)


def _index_old_paths(content: Mapping[Key, dict]) -> dict[str, dict]:
    """git's per-file content of a renamed file, by its old path."""
    return {v["old_path"]: v for k, v in content.items() if not isinstance(k, RelationId) and v.get("old_path")}


def _compare_node(x: str, kind: str, sides: Mapping[str, _Side], content: Mapping[Key, dict], renames: Mapping[str, str],
                  by_old_path: Mapping[str, dict]) -> dict | None:
    """Record what the change did to a node, or return None if it did nothing. The node is added
    or removed when one side has no such node, and the record holds what git says changed inside
    a file, or inside a class's lines."""
    b, h = sides["base"].model.has(x), sides["head"].model.has(x)
    rec: dict = {}
    if not b:
        rec["status"] = "added"
    elif not h:
        rec["status"] = "removed"
    c = content.get(x)
    if c is not None and kind == "file":
        rec.setdefault("status", "same")
        rec.update(c)
    if kind == "file" and rec.get("status") == "removed":
        gone = renames.get(x)
        if gone:
            rec["renamed_to"] = gone
    if kind in schema.SYMBOL_KINDS:
        lines = _list_class_lines(x, sides["base"].symbols.get(x, {}) if b else {}, sides["head"].symbols.get(x, {}) if h else {},
                                  content, by_old_path)
        if lines:
            rec.setdefault("status", "same")
            rec.update(lines)
    if not rec:
        return None
    files = kind == "file" and "git" in rec  # lines are counted by files, so a class's are not counted twice
    rec["totals"] = {"added": rec.get("added", 0) if files else 0, "removed": rec.get("removed", 0) if files else 0,
                     "files": int(files), "new": int(rec["status"] == "added" and kind != "group"),
                     "gone": int(rec["status"] == "removed" and kind != "group")}
    return rec


def _list_class_lines(x: str, bf: Mapping, hf: Mapping, content: Mapping[Key, dict], by_old_path: Mapping[str, dict]) -> dict | None:
    """git's changed lines inside a class's line range, on each side (``bf``, ``hf``: what the
    class's language says about it there)."""
    f = x.file
    c = content.get(f)
    if c is None:  # a class of a renamed file, by its old path
        c = by_old_path.get(f.path)
    if c is None:
        return None
    old_path = c.get("old_path") or f.path
    added = [n for hk in c.get("hunks", []) for n in hk["added"] if hf and hf.get("line", 0) <= n <= (hf.get("end_line") or 0)]
    removed = [n for hk in c.get("hunks", []) for n in hk["removed"]
               if bf and bf.get("path") == old_path and bf.get("line", 0) <= n <= (bf.get("end_line") or 0)]
    if not (added or removed):
        return None
    return {"added": len(added), "removed": len(removed), "added_lines": added, "removed_lines": removed}


def _list_renames(content: Mapping[Key, dict]) -> dict[str, str]:
    """old file -> its new path, for the files git saw renamed (git's per-file content)."""
    return {EntityId.from_path(v["old_path"]): k.path for k, v in content.items() if not isinstance(k, RelationId) and v.get("old_path")}


# ---------- links ----------

class Links:
    """GitHub URLs for a change; every method returns None when the remote is unknown."""

    def __init__(self, change: dict) -> None:
        """``change``: what the change says about itself (``meta["change"]``)."""
        d = change or {}
        self.remote: str | None = d.get("remote")
        self.base: str | None = d.get("base")
        self.head: str | None = d.get("head")
        self.pr: int | None = d.get("pr")

    @staticmethod
    def anchor(path: str) -> str:
        """GitHub's anchor for a file in a diff view: sha256 of its path."""
        return "diff-" + hashlib.sha256(path.encode()).hexdigest()

    def change(self) -> str | None:
        """Return the URL of the PR, or of the compare view of the two commits."""
        if not self.remote:
            return None
        return f"{self.remote}/pull/{self.pr}" if self.pr else f"{self.remote}/compare/{self.base}...{self.head}"

    def diff(self, path: str, line: int | None = None, side: str = "head") -> str | None:
        """Return the URL of a file, and optionally a line on one side, in the PR's files view or
        the compare view."""
        if not self.remote:
            return None
        page = f"{self.remote}/pull/{self.pr}/files" if self.pr else f"{self.remote}/compare/{self.base}...{self.head}"
        return f"{page}#{self.anchor(path)}" + (f"{'R' if side == 'head' else 'L'}{line}" if line else "")

    def blob(self, path: str, side: str = "head", start: int | None = None, end: int | None = None) -> str | None:
        """Return the URL of a file at one commit, optionally pointing at a line range."""
        if not self.remote:
            return None
        sha = self.head if side == "head" else self.base
        frag = f"#L{start}" + (f"-L{end}" if end and end != start else "") if start else ""
        return f"{self.remote}/blob/{sha}/{path}{frag}"


# ---------- the change in numbers ----------

def describe_change(about: dict, content: Mapping[Key, dict]) -> dict:
    """Build the ``meta["change"]`` of a change's model, which scenes drawn from it carry along.
    It holds what the change says about itself (title, pr, description, base, head, remote,
    …), git's totals of files changed and lines added and removed, and a link to it."""
    d = dict(about)
    d.update({"base_short": (d.get("base") or "")[:7], "head_short": (d.get("head") or "")[:7], "files_changed": len(content),
              "added": sum(c.get("added", 0) for c in content.values()), "removed": sum(c.get("removed", 0) for c in content.values())})
    d["link"] = Links(d).change()
    return d


TOTALS = ("added", "removed", "files", "new", "gone")


def _sum_box(el: Element) -> dict | None:
    """Sum up what changed in a drawn box, or return None if nothing did. Give its own status, the
    totals of what changed in what it stands for and in the boxes drawn inside it, and in
    ``internal`` the number of changed dependencies the projection found between what it holds."""
    if el.kind == "anchor":
        return None
    totals = dict.fromkeys(TOTALS, 0)
    own, seen = None, False
    for s in el.sources.get("source", ()):
        rec = s.get("change")
        if rec:
            seen = True
            own = rec if s.key == el.key else own
            for k in TOTALS:
                totals[k] += (rec.get("totals") or {}).get(k, 0)
    for c in el.children:
        rec = c.get("change")
        if rec:
            seen = True
            for k in TOTALS:
                totals[k] += (rec.get("totals") or {}).get(k, 0)
    internal = (el.get("projection") or {}).get("internal_changes", 0)
    own = own or {}
    status = own.get("status") if own.get("status") in ("added", "removed") else "changed" if seen or internal else None
    if status is None:
        return None
    out = {"status": status, **totals, "totals": totals, "internal": internal}
    if el.kind in schema.SYMBOL_KINDS and own.get("status") == "same":
        out["added"], out["removed"] = own.get("added", 0), own.get("removed", 0)  # its own lines; files count them for the module
    for k in ("git", "old_path", "renamed_to"):
        if own.get(k):
            out[k] = own[k]
    return out


def _compare_edge(key: Relation, meta: dict, refs: Mapping[str, list | bool]) -> dict | None:
    """Record what the change did to an edge, judged from the references on each side. ``refs``
    maps each side to its references, or to False where that side has no such edge. The edge is
    added, removed, or changed when the number of references differs; return None when it is the
    same."""
    b, h = refs["base"], refs["head"]
    nb = sum(r["count"] for r in b) if b is not False else 0
    nh = sum(r["count"] for r in h) if h is not False else 0
    if b is False:
        rec = {"status": "added", "base": 0, "head": nh}
    elif h is False:
        rec = {"status": "removed", "base": nb, "head": 0}
    elif nb != nh:
        rec = {"status": "changed", "base": nb, "head": nh}
    else:
        return None
    return rec | {"fact": _describe_fact(key, meta, rec, b if rec["status"] == "removed" else h)}


def _describe_fact(key: Relation, meta: dict, rec: dict, refs: list | bool) -> dict:
    """Describe a changed edge of a change for a tooltip or details panel, with a link to where it changed."""
    first = refs[0] if refs else {}
    path = first.get("path")
    line = first.get("line")
    links = Links(meta.get("change") or {})
    return {"fact": key.id, "status": rec["status"], "kind": key.kind, "subkind": key.subkind, "src": key.src, "dst": key.dst,
            "base": rec.get("base", 0), "head": rec.get("head", 0), "path": path, "line": line,
            "link": links.diff(path, line, "base" if rec["status"] == "removed" else "head") if path else None}


def _sum_edge(el: Element) -> dict | None:
    """Sum up how the references of a drawn edge changed (the projection counted them) and list the
    changed edges it bundles; return None if it did not change."""
    p = el.get("projection") or {}
    if not p.get("status"):
        return None
    changes: list[dict] = []
    for s in el.sources.get("source", ()):
        rec = s.get("change")
        if rec:
            changes.extend(rec["changes"] if "changes" in rec else [rec["fact"]])
    changes.sort(key=lambda c: c["fact"])
    return {"status": p["status"], "base": p.get("base", 0), "head": p.get("head", 0), "facts": [c["fact"] for c in changes],
            "changes": changes[:40], "more": max(0, len(changes) - 40)}


class SidesSaid(Overlay):
    """What extraction said on the sides, as the change's own: for each node and edge, head's
    word, else base's, one per shape (``vocabulary.SHAPES``). Same address, same thing, so a
    map kept for a commit holds for the change's namesake of its node; read from the sides
    when first asked, kept nowhere."""

    def __init__(self, name: str) -> None:
        self.name = name

    def compute(self, element: Element) -> dict | None:
        return element.scene.read_all(self.name).get(element.key)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        model = scene.model
        out: dict[Key, dict] = {}
        for role in ("base", "head"):  # head's word last: it replaces base's
            for k, v in scene.sources[role].read_all(self.name).items():
                if model.has(k):
                    out[k] = v
        return out


def merge_models(key: ModelKey, base: Model, head: Model, meta: dict | None = None) -> Model:
    """Build the model holding every node and edge of either side, each standing for its namesake
    on each side (``union`` says so). Same address, same thing: a node or edge is the same on both sides
    when its address is; a node owned on both sides is owned as head has it."""
    entities = {**base.entities, **head.entities}
    owned = {e.dst for e in head.edges if e.kind == RelationKind.CONTAIN}
    edges = list(head.edges) + [e for e in base.edges if not (e.kind == RelationKind.CONTAIN and e.dst in owned)]
    return Model(key, entities.values(), edges, meta=dict(meta or {}), check=False)


def union(key: ModelKey, meta: dict | None = None, kept: Model | None = None) -> Callable[[Mapping[str, Scene]], Scene]:
    """Merge as a transformation: of two scenes by role (``base``, ``head``), the scene of the
    merged model (``merge_models``, or ``kept``: the same as it was kept at diff time, with
    ``meta`` on top), each element standing for its namesake on each side, and what
    extraction said about it on the sides as the change's (``SidesSaid``, one per shape)."""
    def make(sides: Mapping[str, Scene]) -> Scene:
        if kept is None:
            model = merge_models(key, sides["base"].model, sides["head"].model, meta)
        else:
            model = kept
            model.meta = {**model.meta, **(meta or {})}
        return Scene(model, [SidesSaid(n) for n in vocabulary.SHAPES], sides, dict.fromkeys(sides))

    return make
