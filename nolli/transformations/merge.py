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

import hashlib
from collections import defaultdict

from ..model import CONTAIN, SYMBOL_KINDS, EdgeKey, Model, address
from ..model.scene import Element, Overlay, Scene
from ..store.maps import StoredMaps
from .graph import Graph
from .unfold import index_changes, score_changes  # noqa: F401 (the change's, for callers)


def find_change_scene(el: Element) -> Scene | None:
    """The change an element is of or was drawn from (a projection's source, all the way down)."""
    scene = el.scene
    while scene is not None:
        if "base" in scene.sources:
            return scene
        scene = scene.sources.get("source")
    return None


def is_change(scene: Scene) -> bool:
    return "base" in scene.sources


class Change:
    name = "change"

    def __init__(self, content: StoredMaps) -> None:
        self.content = content

    def compute(self, el: Element) -> dict | None:
        if is_change(el.scene):
            return self.compare(el)
        if "source" in el.scene.sources and find_change_scene(el) is not None:
            return describe_edge(el) if el.is_edge else describe_box(el)
        return None

    # -- on a change's own elements --------------------------------------------------

    def compare(self, el: Element) -> dict | None:
        b, h = el.find_same("base"), el.find_same("head")
        if el.is_edge:
            nb = (b.get("facts") or {}).get("count", 0) if b else 0
            nh = (h.get("facts") or {}).get("count", 0) if h else 0
            if b is None:
                return {"status": "added", "base": 0, "head": nh}
            if h is None:
                return {"status": "removed", "base": nb, "head": 0}
            return {"status": "changed", "base": nb, "head": nh} if nb != nh else None
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
        return rec or None

    def list_class_lines(self, el: Element, b: Element | None, h: Element | None, content) -> dict | None:
        f = address.file_of(el.key)
        c = content.get(f)
        if c is None:  # a class of a renamed file, by its old path
            c = next((v for k, v in content.items() if isinstance(k, str) and v.get("old_path") == address.path_of(f)), None)
        if c is None:
            return None
        hf, bf = (h.get("facts") or {}) if h else {}, (b.get("facts") or {}) if b else {}
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

    def __init__(self, scene: Scene) -> None:
        d = (scene.model.meta or {}).get("change") or {}
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

def summarize_change(scene: Scene) -> dict:
    """What the change says about itself, with git's totals: files changed, lines added and removed."""
    change = scene.overlays.get("change")
    content = change.content.load(scene.model.key) if isinstance(change, Change) else {}
    d = dict(scene.model.meta.get("change") or {})
    d.update({"base_short": (d.get("base") or "")[:7], "head_short": (d.get("head") or "")[:7], "files_changed": len(content),
              "added": sum(c.get("added", 0) for c in content.values()), "removed": sum(c.get("removed", 0) for c in content.values())})
    return d


def count_changes(scene: Scene) -> dict:
    """How many entities and relations changed, by kind and status (a report's numbers)."""
    g = Graph(scene)
    by: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for x in g.nodes:
        d = g.diff(x)
        if d:
            by["entities"][f"{g.kind(x)}/{d['status']}"] += 1
            if (g.kind(x) == "file" or g.kind(x) in SYMBOL_KINDS) and (d.get("added") or d.get("removed")):
                by["content"][g.kind(x)] += 1
    for r in g.relations:
        if r.diff:
            by["relations"][r.diff["status"]] += 1
    out = {k: dict(sorted(v.items())) for k, v in sorted(by.items())}
    out["files_changed"] = summarize_change(scene)["files_changed"]
    return out


def describe_node(scene: Scene, x: str, internal: int = 0) -> dict | None:
    """What the viewer shows on a box of a change: its status and the totals inside it."""
    if address.is_own_code(x) or not scene.has(x):
        return None
    g = Graph(scene)
    own = g.diff(x)
    agg = index_changes(scene).get(x)
    status = own.get("status") if own.get("status") in ("added", "removed") else None
    if status is None and (agg or internal):
        status = "changed"
    if status is None:
        return None
    out = {"status": status, **(agg or {"added": 0, "removed": 0, "files": 0, "new": 0, "gone": 0})}
    if g.kind(x) in SYMBOL_KINDS and own.get("status") == "same":
        out["added"], out["removed"] = own.get("added", 0), own.get("removed", 0)  # its own lines; files count them for the module
    out["internal"] = internal
    for k in ("git", "old_path", "renamed_to"):
        if own.get(k):
            out[k] = own[k]
    return out


def describe_box(el: Element) -> dict | None:
    """A drawn box: describe_node of the node it is, with the changes between what it holds."""
    change = find_change_scene(el)
    p = el.get("projection") or {}
    return describe_node(change, el.key, p.get("internal_changes", 0)) if change is not None else None


def describe_fact(scene: Scene, key: EdgeKey, links: Links) -> list[dict]:
    """The changed facts of one edge of a change, each with a link to where it changed."""
    el = scene.find_element(key)
    d = el.get("change")
    if not d:
        return []
    side = el.find_same("base" if d["status"] == "removed" else "head")
    facts = ((side.get("facts") or {}).get("facts") or []) if side else []
    g = Graph(scene)
    first = facts[0] if facts else {}
    path = first.get("path") or (g.attrs(key.src).get("path") if g.has(key.src) else None)
    line = first.get("line")
    return [{"fact": key.format(), "status": d["status"], "kind": key.kind, "subkind": key.subkind, "src": key.src, "dst": key.dst,
             "base": d.get("base", 0), "head": d.get("head", 0), "path": path, "line": line,
             "link": links.diff(path, line, "base" if d["status"] == "removed" else "head") if path else None}]


def describe_edge(el: Element) -> dict | None:
    """A drawn edge: how its references changed, and the changed facts it bundles."""
    p = el.get("projection") or {}
    status = p.get("status")
    change = find_change_scene(el)
    if not status or change is None:
        return None
    links = Links(change)
    changed = sorted(p.get("changed") or [])
    facts = [c for f in changed[:40] for c in describe_fact(change, EdgeKey.parse(f), links) if change.has(EdgeKey.parse(f))]
    return {"status": status, "base": p.get("base", 0), "head": p.get("head", 0), "facts": changed,
            "changes": facts, "more": max(0, len(changed) - 40)}




def merge(base: Scene, head: Scene, key: str, meta: dict | None = None, overlays: list[Overlay] = ()) -> Scene:
    """The change from ``base`` to ``head`` as a scene: the overlays of both sides (head's of a
    name win) and ``overlays``."""
    kinds = {**base.model.kinds, **head.model.kinds}
    owned = {e.dst for e in head.model.edges if e.kind == CONTAIN}
    edges = list(head.model.edges) + [e for e in base.model.edges if not (e.kind == CONTAIN and e.dst in owned)]
    model = Model(key, kinds, edges, refs={"base": None, "head": None}, meta=dict(meta or {}), check=False)
    return Scene(model, [*base.overlays.values(), *head.overlays.values(), *overlays], {"base": base, "head": head})
