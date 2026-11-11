"""Lists what changed in a change, as a reviewer goes through it.

Everything here reads a change's scene and returns plain data for a reviewer, person or agent.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from nolli.logic.transformations import merge, projection
from nolli.logic.transformations.merge import Links
from nolli.logic.transformations.projection.reading import Reading
from nolli.model import schema
from nolli.model.scene import Scene
from nolli.model.schema import EntityId
from nolli.review.reading import Reader

# ---------- the numbered changes a reviewer (person or agent) goes through ----------

def _find_module(g: Reader, x: str) -> str:
    """Return the module (file) a fact end belongs to; anything outside files is returned as it is."""
    cur: str | None = x
    while cur is not None:
        if g.has(cur) and g.kind(cur) == "file":
            return cur
        cur = g.parent(cur)
    return x


def _find_area(g: Reader, x: str) -> str:
    """Return the top-level box a node sits in, which is a top-level directory or a group."""
    cur, last = x, x
    while cur is not None:
        p = g.parent(cur)
        if p is None or g.attrs(p).get("root"):
            return cur if p is not None else last
        last, cur = cur, p
    return last


def format_name(g: Reader, x: str) -> str:
    """Return a readable name for a node. Files and directories are named by their path, classes
    (and the other symbols) by path#Name."""
    if not g.has(x):
        return x
    kind, a = g.kind(x), g.attrs(x)
    if kind in ("file", "directory"):
        return a.get("path") or g.name(x)
    if kind in schema.SYMBOL_KINDS:
        return f"{a.get('path')}#{g.name(x)}"
    return g.name(x)


def _find_role(g: Reader, x: str) -> str:
    """Return the role of the file or directory a node belongs to (source, test, docs, …). Nodes
    outside the file tree (services, endpoints, packages) count as source."""
    cur: str | None = x
    while cur is not None:
        if g.has(cur) and g.kind(cur) in ("file", "directory") and g.attrs(cur).get("role"):
            r = g.attrs(cur)["role"]
            return "source" if r == "mixed" else r
        cur = g.parent(cur)
    return "source"


def list_ancestors_to_unfold(g: Reader, x: str) -> list[str]:
    """List the boxes to unfold so that x itself is drawn."""
    return list(g.ancestors(x))[::-1]


def list_changes(scene: Scene) -> list[dict]:
    """Return a change's numbered changes in their order, as the ``review`` overlay keeps them
    (computed by ``number_changes`` once per change)."""
    listed = [it for v in scene.read_all("review").values() for it in v.get("listed", ())]
    return sorted(listed, key=lambda it: int(it["id"][1:]))


def number_changes(scene: Scene) -> list[dict]:
    """List a change's changes and number them C1, C2, … in a stable order. Changes to source code
    come first, then those to tests, docs and the rest. Within each, components added or removed
    come first (a new module once, with its classes, not once per class), then dependencies
    between modules, then edited files. Each item names the nodes and facts it is about, so a
    verdict on it can be shown on whatever box or edge contains them. The ``review`` overlay
    computes the list and keeps it; readers take it from there (``list_changes``)."""
    g = Reader(scene)
    links = Links(scene.model.meta.get("change") or {})
    comps, deps, content = [], [], []
    for x in sorted(g.nodes):
        d = g.diff(x)
        st = d.get("status")
        kind = g.kind(x)
        if st not in ("added", "removed") or kind in ("group", "unresolved"):
            continue
        p = g.parent(x)
        if p is not None and g.diff(p).get("status") == st:
            continue  # covered by its parent's item
        inner = [c for c in g.descendants(x) if g.diff(c).get("status") == st]
        classes = [g.name(c) for c in inner if g.kind(c) in schema.SYMBOL_KINDS] if kind not in schema.SYMBOL_KINDS else []
        lines = d.get("added", 0) if st == "added" else d.get("removed", 0)
        if kind == "directory":
            lines = sum(g.diff(c).get("added" if st == "added" else "removed", 0) for c in inner if g.kind(c) == "file")
        what = {"file": "module", "directory": "package", "package": "external package"}.get(kind, kind)
        title = f"{'new' if st == 'added' else 'removed'} {what} {format_name(g, x)}"
        if d.get("old_path") or d.get("renamed_to"):
            title += f" (renamed {'from ' + d['old_path'] if d.get('old_path') else 'to ' + d['renamed_to']})"
        a = g.attrs(x)
        path = a.get("path")
        comps.append({"category": "component", "status": st, "title": title, "area": _find_area(g, x), "role": _find_role(g, x),
                      "classes": classes, "lines": lines, "entities": [x] + inner, "facts": [],
                      "reveal": list_ancestors_to_unfold(g, x), "focus": [x],
                      "link": (links.diff(path) if kind == "file" else links.blob(path, "base" if st == "removed" else "head",
                                                                                a.get("line"), a.get("end_line")))
                      if path else None})
    groups: dict[tuple, list] = defaultdict(list)
    totals: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])  # all references between the two modules, before and after
    for r in g.relations:
        a, b = _find_module(g, r.src), _find_module(g, r.dst)
        if r.kind == "provide":
            a, b = b, a  # drawn interface -> implementation
        if a == b:
            continue  # inside one module: shows as its content
        sub = "import" if r.subkind in ("ref", "import") else r.subkind
        t = totals[(a, b, r.kind, sub)]
        t[0] += r.diff.get("base", 0) if r.diff else r.count
        t[1] += r.diff.get("head", 0) if r.diff else r.count
        if r.diff:
            groups[(a, b, r.kind, sub)].append(r)
    for (a, b, kind, sub), rs in sorted(groups.items()):
        base, head = totals[(a, b, kind, sub)]
        st = "added" if base == 0 else "removed" if head == 0 else "changed"
        word = {"added": "new dependency", "removed": "dependency removed", "changed": "dependency changed"}[st]
        if kind == "provide":
            word = {"added": "now realised by", "removed": "no longer realised by", "changed": "realisation changed"}[st]
        targets = sorted({f"{'+' if r.diff['status'] == 'added' else '−' if r.diff['status'] == 'removed' else '~'}"
                          f"{format_name(g, r.dst if kind != 'provide' else r.src)}" for r in rs})
        changes = sorted((r.diff["fact"] for r in rs),
                         key=lambda c: ({"added": 0, "removed": 1, "changed": 2}[c["status"]], c["dst"]))
        first = changes[0]
        role = _find_role(g, a)
        deps.append({"category": "dependency", "status": st, "title": f"{format_name(g, a)} → {format_name(g, b)}: {word}",
                     "area": _find_area(g, a), "role": role if role != "source" else _find_role(g, b),
                     "kind": kind, "subkind": sub, "base": base, "head": head,
                     "targets": targets[:12], "entities": [a, b], "facts": [r.key.format() for r in rs],
                     "reveal": sorted(set(list_ancestors_to_unfold(g, a)) | set(list_ancestors_to_unfold(g, b))), "focus": [a, b],
                     "link": first["link"], "changes": changes[:12], "more": max(0, len(changes) - 12)})
    for x in sorted(g.nodes):
        d = g.diff(x)
        if g.kind(x) != "file" or "git" not in d or d.get("status") in ("added", "removed"):
            continue
        cls = [{"name": g.name(c), "added": g.diff(c).get("added", 0), "removed": g.diff(c).get("removed", 0)}
               for c in g.children(x) if g.diff(c).get("status") == "same" and (g.diff(c).get("added") or g.diff(c).get("removed"))]
        path = g.attrs(x).get("path")
        hunks = [{"new": h["new"], "old": h["old"], "added": len(h["added"]), "removed": len(h["removed"]),
                  "link": links.diff(path, (h["added"] or [None])[0] or (h["removed"] or [None])[0], "head" if h["added"] else "base")}
                 for h in d.get("hunks", [])] if path else []
        content.append({"category": "content", "status": "changed", "title": f"edited {path} (+{d.get('added', 0)} −{d.get('removed', 0)})",
                        "area": _find_area(g, x), "role": _find_role(g, x), "added": d.get("added", 0), "removed": d.get("removed", 0),
                        "classes": cls, "entities": [x], "facts": [], "hunks": hunks[:30], "more_hunks": max(0, len(hunks) - 30),
                        "reveal": list_ancestors_to_unfold(g, x), "focus": [x], "link": links.diff(path) if path else None})
    items = []
    for source in (True, False):  # source changes first in every category, then tests, docs, config, data
        for group in (comps, deps, content):
            part = [it for it in group if (it["role"] == "source") == source]
            part.sort(key=lambda it: (it["role"], it["area"], it["title"]))
            items.extend(part)
    for i, it in enumerate(items, 1):
        it["id"] = f"C{i}"
    return items


def _find_review_cut(scene: Scene) -> list[str]:
    """Find the boxes to unfold so that every change in source code is drawn, by joining what
    going to each of them unfolds. The review diagram and the viewer's "all changes" unfold them
    (``Changes``)."""
    out = {EntityId.REPO_ROOT}
    for it in list_changes(scene):
        if it["role"] == "source":
            out.update(it["reveal"])
    return sorted(out)


@dataclass(frozen=True)
class Changes(projection.Expansion):
    """The viewer's "all changes": for a change, every box ``_find_review_cut`` names unfolded;
    for anything else, the default cut. Here, beside the numbered changes it reads, until they
    move to logic."""
    name = "changes"

    def find(self, g: Reading, hidden: set[str], st: projection.ProjectionSpec) -> set[str]:
        if not merge.is_change(g.scene):
            return projection.AtMost().find(g, hidden, st)
        return {EntityId.REPO_ROOT, *_find_review_cut(g.scene)}

    def name_for(self, g: Reading) -> str | None:
        return self.name if merge.is_change(g.scene) else projection.AtMost.name


PRESETS: dict[str, type[projection.Expansion]] = {**projection.PRESETS, "changes": Changes}  # every preset the viewer offers
