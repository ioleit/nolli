"""Merge: two models of one repository, base and head, into the model of the change between
them, and the overlay that says what the change did (``change``).

Same address, same thing: the change holds every node and edge of either side, and each
refers to its namesake in ``base`` and in ``head`` (where it has one). What changed is the
``change`` overlay's to say: on the change's own nodes and edges it compares what the two
sides' overlays say (a node or edge on one side only is ``added`` or ``removed``, an edge
whose number of references differs ``changed``); what changed inside a file is git's word,
kept with the change as its ``content`` map (``nolli.build.list_content``): a file gets git's
status and its added and removed lines, a class the changed lines inside its line range. On a
drawn box or edge of a picture of the change it sums up what changed underneath. Links into
the PR and the change's totals are here too.
"""

from __future__ import annotations

from collections.abc import Mapping

import hashlib
from collections import defaultdict

from ..model import CONTAIN, SYMBOL_KINDS, EdgeKey, Model, address
from ..model.scene import Element, Overlay, Scene
from ..store.maps import StoredMaps
from ..model.vocabulary import count_references, read_references, read_symbols


def is_change(scene: Scene) -> bool:
    return "base" in scene.sources


class Change:
    name = "change"

    def __init__(self, content: StoredMaps) -> None:
        self.content = content

    def compute(self, el: Element) -> dict | None:
        if is_change(el.scene):
            return self.compare(el)
        if "source" in el.scene.sources:  # a picture: what changed underneath, from what it stands for
            return sum_edge(el) if el.is_edge else sum_box(el)
        return None

    # -- on a change's own elements --------------------------------------------------

    def compare(self, el: Element) -> dict | None:
        b, h = el.find_same("base"), el.find_same("head")
        if el.is_edge:
            nb = count_references(b) if b else 0
            nh = count_references(h) if h else 0
            if b is None:
                rec = {"status": "added", "base": 0, "head": nh}
            elif h is None:
                rec = {"status": "removed", "base": nb, "head": 0}
            elif nb != nh:
                rec = {"status": "changed", "base": nb, "head": nh}
            else:
                return None
            return rec | {"fact": describe_fact(el, rec, b, h)}
        content = self.content.load(el.model_key)
        rec: dict = {}
        if b is None:
            rec["status"] = "added"
        elif h is None:
            rec["status"] = "removed"
        c = content.get(el.key)
        if c is not None and el.kind == "file":
            rec.setdefault("status", "same")
            rec.update(c)
        if el.kind == "file" and rec.get("status") == "removed":
            gone = el.scene.memo(list_renames).get(el.key)
            if gone:
                rec["renamed_to"] = gone
        if el.kind in SYMBOL_KINDS:
            lines = self.list_class_lines(el, b, h, content)
            if lines:
                rec.setdefault("status", "same")
                rec.update(lines)
        if not rec:
            return None
        files = el.kind == "file" and "git" in rec  # lines are counted by files, so a class's are not counted twice
        rec["totals"] = {"added": rec.get("added", 0) if files else 0, "removed": rec.get("removed", 0) if files else 0,
                         "files": int(files), "new": int(rec["status"] == "added" and el.kind != "group"),
                         "gone": int(rec["status"] == "removed" and el.kind != "group")}
        return rec

    def list_class_lines(self, el: Element, b: Element | None, h: Element | None, content) -> dict | None:
        f = address.file_of(el.key)
        c = content.get(f)
        if c is None:  # a class of a renamed file, by its old path
            c = next((v for k, v in content.items() if isinstance(k, str) and v.get("old_path") == address.path_of(f)), None)
        if c is None:
            return None
        hf, bf = read_symbols(h) if h else {}, read_symbols(b) if b else {}
        old_path = c.get("old_path") or address.path_of(f)
        added = [n for hk in c.get("hunks", []) for n in hk["added"] if hf and hf.get("line", 0) <= n <= (hf.get("end_line") or 0)]
        removed = [n for hk in c.get("hunks", []) for n in hk["removed"]
                   if bf and bf.get("path") == old_path and bf.get("line", 0) <= n <= (bf.get("end_line") or 0)]
        if not (added or removed):
            return None
        return {"added": len(added), "removed": len(removed), "added_lines": added, "removed_lines": removed}


def list_renames(scene: Scene) -> dict[str, str]:
    """old file -> its new path, for the files git saw renamed."""
    change = scene.overlays.get("change")
    content = change.content.load(scene.model.key) if isinstance(change, Change) else {}
    return {address.repo(v["old_path"]): address.path_of(k) for k, v in content.items() if isinstance(k, str) and v.get("old_path")}


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
        """The PR, or the compare view of the two commits."""
        if not self.remote:
            return None
        return f"{self.remote}/pull/{self.pr}" if self.pr else f"{self.remote}/compare/{self.base}...{self.head}"

    def diff(self, path: str, line: int | None = None, side: str = "head") -> str | None:
        """A file (and a line on one side) in the PR's files view or the compare view."""
        if not self.remote:
            return None
        page = f"{self.remote}/pull/{self.pr}/files" if self.pr else f"{self.remote}/compare/{self.base}...{self.head}"
        return f"{page}#{self.anchor(path)}" + (f"{'R' if side == 'head' else 'L'}{line}" if line else "")

    def blob(self, path: str, side: str = "head", start: int | None = None, end: int | None = None) -> str | None:
        """A file at one commit, optionally a line range."""
        if not self.remote:
            return None
        sha = self.head if side == "head" else self.base
        frag = f"#L{start}" + (f"-L{end}" if end and end != start else "") if start else ""
        return f"{self.remote}/blob/{sha}/{path}{frag}"


# ---------- the change in numbers ----------

def describe_change(about: dict, content: Mapping[Key, dict]) -> dict:
    """What a change says about itself (title, pr, description, base, head, remote, …), with
    git's totals (files changed, lines added and removed) and the link to it: its model's
    ``meta["change"]``, which every picture of it carries along."""
    d = dict(about)
    d.update({"base_short": (d.get("base") or "")[:7], "head_short": (d.get("head") or "")[:7], "files_changed": len(content),
              "added": sum(c.get("added", 0) for c in content.values()), "removed": sum(c.get("removed", 0) for c in content.values())})
    d["link"] = Links(d).change()
    return d


TOTALS = ("added", "removed", "files", "new", "gone")


def sum_box(el: Element) -> dict | None:
    """A drawn box: its own status, and the totals of what changed in what it stands for and in
    the boxes drawn inside it; ``internal``, the changed dependencies the projection found
    between what it holds."""
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
    if el.kind in SYMBOL_KINDS and own.get("status") == "same":
        out["added"], out["removed"] = own.get("added", 0), own.get("removed", 0)  # its own lines; files count them for the module
    for k in ("git", "old_path", "renamed_to"):
        if own.get(k):
            out[k] = own[k]
    return out


def describe_fact(el: Element, rec: dict, b: Element | None, h: Element | None) -> dict:
    """A changed edge of a change for a tooltip or details panel, with a link to where it changed."""
    side = b if rec["status"] == "removed" else h
    refs = read_references(side) if side else []
    first = refs[0] if refs else {}
    path = first.get("path")
    line = first.get("line")
    key = el.key
    links = Links(el.meta.get("change") or {})
    return {"fact": key.format(), "status": rec["status"], "kind": key.kind, "subkind": key.subkind, "src": key.src, "dst": key.dst,
            "base": rec.get("base", 0), "head": rec.get("head", 0), "path": path, "line": line,
            "link": links.diff(path, line, "base" if rec["status"] == "removed" else "head") if path else None}


def sum_edge(el: Element) -> dict | None:
    """A drawn edge: how its references changed (the projection counted them), and the changed
    edges it bundles."""
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


def union(key: str, meta: dict | None = None):
    """Merge as a transformation: of two scenes by role (``base``, ``head``), the model holding
    every node and edge of either, each referring to its namesake on each side."""
    def make(sides: dict[str, Scene]) -> Model:
        base, head = sides["base"], sides["head"]
        kinds = {**base.model.kinds, **head.model.kinds}
        owned = {e.dst for e in head.model.edges if e.kind == CONTAIN}
        edges = list(head.model.edges) + [e for e in base.model.edges if not (e.kind == CONTAIN and e.dst in owned)]
        return Model(key, kinds, edges, refs={"base": None, "head": None}, meta=dict(meta or {}), check=False)

    return make
