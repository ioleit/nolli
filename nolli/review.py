"""Reviewing a diff model (``nolli diff``): links, change totals per box, details for one
box, and where the default view should unfold first.

First version, written together with the viewer's review mode; everything here is a
guess at what a reviewer needs, to be revised once it has been used on real PRs.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .model import CONTAIN, Model
from .rollup import fact_key


def is_diff(m: Model) -> bool:
    return bool((m.meta or {}).get("diff"))


# ---------- links ----------

class Links:
    """GitHub URLs for a diff model; every method returns None when the remote is unknown."""

    def __init__(self, m: Model) -> None:
        d = (m.meta or {}).get("diff") or {}
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


# ---------- totals per box ----------

def diff_index(m: Model) -> dict[str, dict]:
    """Per entity, the changes in its subtree (itself included): changed lines and files
    (from file records, so class lines are not counted twice), entities added and removed.
    Cached on the model."""
    cached = m.__dict__.get("_diff_index")
    if cached is not None:
        return cached
    idx: dict[str, dict] = defaultdict(lambda: {"added": 0, "removed": 0, "files": 0, "new": 0, "gone": 0})
    for e in m.entities.values():
        d = e.attrs.get("diff")
        if not d:
            continue
        lines = e.kind == "file" and ("git" in d)
        cur: str | None = e.id
        while cur is not None:
            a = idx[cur]
            if lines:
                a["added"] += d.get("added", 0)
                a["removed"] += d.get("removed", 0)
                a["files"] += 1
            if d["status"] == "added" and e.kind != "group":
                a["new"] += 1
            elif d["status"] == "removed" and e.kind != "group":
                a["gone"] += 1
            cur = m.parent(cur)
    m.__dict__["_diff_index"] = dict(idx)
    return m.__dict__["_diff_index"]


def node_diff(m: Model, x: str, internal_changes: dict[str, str] | None = None) -> dict | None:
    """What the viewer shows on a drawn box: its status and the totals inside it."""
    if x.startswith("body:"):
        return None
    e = m.entities[x]
    own = e.attrs.get("diff") or {}
    agg = diff_index(m).get(x)
    status = own.get("status") if own.get("status") in ("added", "removed") else None
    if status is None and (agg or internal_changes):
        status = "changed"
    if status is None:
        return None
    out = {"status": status, **(agg or {"added": 0, "removed": 0, "files": 0, "new": 0, "gone": 0})}
    if e.kind == "class" and own.get("status") == "same":
        out["added"], out["removed"] = own.get("added", 0), own.get("removed", 0)  # its own lines; files count them for the module
    out["internal"] = len(internal_changes or {})
    for k in ("git", "old_path", "renamed_to"):
        if own.get(k):
            out[k] = own[k]
    return out


def fact_change(m: Model, r, links: Links) -> dict:
    """One changed fact for a tooltip or details panel, with a link to where it changed."""
    d = r.attrs["diff"]
    p = r.provenance
    side = "base" if d["status"] == "removed" else "head"
    path = p.path if p and p.path else (m.entities[r.src].attrs.get("path") if m.has(r.src) else None)
    return {"fact": fact_key(r), "status": d["status"], "kind": r.kind, "subkind": r.subkind, "src": r.src, "dst": r.dst,
            "base": d.get("base", 0), "head": d.get("head", 0), "path": path, "line": p.line if p else None,
            "link": links.diff(path, p.line if p else None, side) if path else None}


# ---------- default view: unfold towards the changes ----------

def change_scores(m: Model, hidden: set[str]) -> dict[str, float]:
    """Per container, how much unfolding it shows about the change: changed relations that
    run between its children (weighted), plus children that contain changes."""
    idx = diff_index(m)
    score: dict[str, float] = defaultdict(float)
    chain_cache: dict[str, list[str] | None] = {}

    def chain(x: str) -> list[str] | None:
        if x in chain_cache:
            return chain_cache[x]
        out, cur = [], x
        while cur is not None:
            if cur in hidden:
                chain_cache[x] = None
                return None
            out.append(cur)
            cur = m.parent(cur)
        chain_cache[x] = out
        return out

    for r in m.relations:
        if r.kind == CONTAIN or not r.attrs.get("diff"):
            continue
        cs, cd = chain(r.src), chain(r.dst)
        if not cs or not cd:
            continue
        common = next((a for a in cs if a in set(cd)), None)
        if common is not None and common not in (r.src, r.dst):
            score[common] += 3
    for x in idx:
        if x in hidden:
            continue
        p = m.parent(x)
        if p is not None and p not in hidden:
            score[p] += 1  # unfolding p tells its changed child apart from the others
    return dict(score)


# ---------- details for one box ----------

MAX_ITEMS = 60


def details(m: Model, x: str) -> dict:
    """Everything a reviewer may want about one box: changed files with hunks and links,
    classes with changed lines, entities added or removed inside it, and the changed
    dependencies that start or end inside it."""
    links = Links(m)
    target = x[5:] if x.startswith("body:") else x
    sub = {target} | set(m.descendants(target))
    files, classes, entities = [], [], []
    for eid in sorted(sub):
        e = m.entities[eid]
        d = e.attrs.get("diff")
        if not d:
            continue
        if e.kind == "file" and "git" in d:
            path = e.attrs.get("path", eid[3:])
            hunks = [{"new": h["new"], "old": h["old"], "added": len(h["added"]), "removed": len(h["removed"]),
                      "link": links.diff(path, (h["added"] or [None])[0] or (h["removed"] or [None])[0],
                                         "head" if h["added"] else "base")} for h in d.get("hunks", [])]
            files.append({"id": eid, "path": path, "git": d["git"], "old_path": d.get("old_path"), "added": d.get("added", 0),
                          "removed": d.get("removed", 0), "binary": d.get("binary", False), "link": links.diff(path),
                          "blob": links.blob(path, "base" if d["git"] == "D" else "head"), "hunks": hunks[:20],
                          "more_hunks": max(0, len(hunks) - 20)})
        elif e.kind == "class" and (d.get("added") or d.get("removed")) and d["status"] == "same":
            path = e.attrs.get("path")
            first = (d.get("added_lines") or [None])[0]
            classes.append({"id": eid, "name": e.name, "path": path, "added": d.get("added", 0), "removed": d.get("removed", 0),
                            "lines": [e.attrs.get("line"), e.attrs.get("end_line")],
                            "link": links.diff(path, first) if path else None})
        if d["status"] in ("added", "removed"):
            path = e.attrs.get("path")
            entities.append({"id": eid, "kind": e.kind, "name": e.name, "status": d["status"], "path": path,
                             "link": (links.blob(path, "base" if d["status"] == "removed" else "head", e.attrs.get("line"),
                                                 e.attrs.get("end_line")) if path else None)})
    deps = [fact_change(m, r, links) for r in m.relations
            if r.kind != CONTAIN and r.attrs.get("diff") and (r.src in sub or r.dst in sub)]
    deps.sort(key=lambda c: ({"added": 0, "removed": 1, "changed": 2}[c["status"]], c["src"], c["dst"]))
    e = m.entities[target]
    return {"id": x, "kind": e.kind, "name": e.name, "path": e.attrs.get("path"), "diff": node_diff(m, target),
            "files": files[:MAX_ITEMS], "more_files": max(0, len(files) - MAX_ITEMS),
            "classes": classes[:MAX_ITEMS], "entities": entities[:MAX_ITEMS], "more_entities": max(0, len(entities) - MAX_ITEMS),
            "dependencies": deps[:MAX_ITEMS], "more_dependencies": max(0, len(deps) - MAX_ITEMS),
            "link": links.diff(e.attrs["path"]) if e.kind == "file" and e.attrs.get("path") else None}
