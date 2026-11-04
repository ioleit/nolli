"""``change``: what a change did, for a change's scene (``nolli/transform/merge.py``) and for
any projection of one.

On a change's own nodes and edges it compares the two sides: a node or edge on one side
only is ``added`` or ``removed``, an edge whose number of references differs ``changed``.
What changed inside a file is git's word, kept with the change as the ``content`` map
(its hunks, ``nolli.build.list_content``): a file gets git's status and its added and
removed lines, a class the changed lines inside its line range (head lines from its head
range, removed lines from its base range). On a drawn box or edge it sums up what changed
underneath (``describe_box``), using the projection's own record of the facts a line bundles.

The rest are the change as data, for the viewer, a review agent and GitHub links: totals
per box, the numbered changes (``list_changes``), details for one box, links into the PR.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from ..model import SYMBOL_KINDS, EdgeKey, address
from ..scene import Element, Scene, StoredMaps
from ..transform.graph import Graph


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


def index_changes(scene: Scene) -> dict[str, dict]:
    """Per node of a change, the changes in its subtree (itself included): changed lines and
    files (from file records, so class lines are not counted twice), nodes added and removed."""
    return scene.memo(_index_changes)


def _index_changes(scene: Scene) -> dict[str, dict]:
    g = Graph(scene)
    idx: dict[str, dict] = defaultdict(lambda: {"added": 0, "removed": 0, "files": 0, "new": 0, "gone": 0})
    for x in g.nodes:
        d = g.diff(x)
        if not d:
            continue
        lines = g.kind(x) == "file" and ("git" in d)
        cur: str | None = x
        while cur is not None:
            a = idx[cur]
            if lines:
                a["added"] += d.get("added", 0)
                a["removed"] += d.get("removed", 0)
                a["files"] += 1
            if d["status"] == "added" and g.kind(x) != "group":
                a["new"] += 1
            elif d["status"] == "removed" and g.kind(x) != "group":
                a["gone"] += 1
            cur = g.parent(cur)
    return dict(idx)


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


# ---------- unfolding towards the changes ----------

def score_changes(scene: Scene, hidden: set[str]) -> dict[str, float]:
    """Per container, how much unfolding it shows about the change: changed relations that
    run between its children (weighted), plus children that contain changes."""
    g = Graph(scene)
    idx = index_changes(scene)
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
            cur = g.parent(cur)
        chain_cache[x] = out
        return out

    for r in g.relations:
        if not r.diff:
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
        p = g.parent(x)
        if p is not None and p not in hidden:
            score[p] += 1  # unfolding p tells its changed child apart from the others
    return dict(score)


# ---------- details for one box ----------

MAX_ITEMS = 60


def detail_box(scene: Scene, x: str) -> dict | None:
    """Everything a reviewer may want about one box of a change: changed files with hunks and
    links, classes with changed lines, nodes added or removed inside it, and the changed
    dependencies that start or end inside it. None if the change has no such box."""
    target = address.owner(x)
    if not scene.has(target):
        return None
    g = Graph(scene)
    links = Links(scene)
    sub = {target} | set(g.descendants(target))
    files, classes, entities = [], [], []
    for eid in sorted(sub):
        d = g.diff(eid)
        if not d:
            continue
        a, kind = g.attrs(eid), g.kind(eid)
        if kind == "file" and "git" in d:
            path = a.get("path", address.path_of(eid))
            hunks = [{"new": h["new"], "old": h["old"], "added": len(h["added"]), "removed": len(h["removed"]),
                      "link": links.diff(path, (h["added"] or [None])[0] or (h["removed"] or [None])[0],
                                         "head" if h["added"] else "base")} for h in d.get("hunks", [])]
            files.append({"id": eid, "path": path, "git": d["git"], "old_path": d.get("old_path"), "added": d.get("added", 0),
                          "removed": d.get("removed", 0), "binary": d.get("binary", False), "link": links.diff(path),
                          "blob": links.blob(path, "base" if d["git"] == "D" else "head"), "hunks": hunks[:20],
                          "more_hunks": max(0, len(hunks) - 20)})
        elif kind in SYMBOL_KINDS and (d.get("added") or d.get("removed")) and d["status"] == "same":
            path = a.get("path")
            first = (d.get("added_lines") or [None])[0]
            classes.append({"id": eid, "name": g.name(eid), "path": path, "added": d.get("added", 0), "removed": d.get("removed", 0),
                            "lines": [a.get("line"), a.get("end_line")], "link": links.diff(path, first) if path else None})
        if d["status"] in ("added", "removed"):
            path = a.get("path")
            entities.append({"id": eid, "kind": kind, "name": g.name(eid), "status": d["status"], "path": path,
                             "link": (links.blob(path, "base" if d["status"] == "removed" else "head", a.get("line"),
                                                 a.get("end_line")) if path else None)})
    deps = [c for r in g.relations if r.diff and (r.src in sub or r.dst in sub) for c in describe_fact(scene, r.key, links)]
    deps.sort(key=lambda c: ({"added": 0, "removed": 1, "changed": 2}[c["status"]], c["src"], c["dst"]))
    a = g.attrs(target)
    return {"id": x, "kind": g.kind(target), "name": g.name(target), "path": a.get("path"), "diff": describe_node(scene, target),
            "files": files[:MAX_ITEMS], "more_files": max(0, len(files) - MAX_ITEMS),
            "classes": classes[:MAX_ITEMS], "entities": entities[:MAX_ITEMS], "more_entities": max(0, len(entities) - MAX_ITEMS),
            "dependencies": deps[:MAX_ITEMS], "more_dependencies": max(0, len(deps) - MAX_ITEMS),
            "link": links.diff(a["path"]) if g.kind(target) == "file" and a.get("path") else None}


# ---------- the numbered changes a reviewer (person or agent) goes through ----------

def find_module(g: Graph, x: str) -> str:
    """The module (file) a fact end belongs to; anything outside files stays itself."""
    cur: str | None = x
    while cur is not None:
        if g.has(cur) and g.kind(cur) == "file":
            return cur
        cur = g.parent(cur)
    return x


def find_area(g: Graph, x: str) -> str:
    """The top-level box a node sits in: a top-level directory, or a group."""
    cur, last = x, x
    while cur is not None:
        p = g.parent(cur)
        if p is None or g.attrs(p).get("root"):
            return cur if p is not None else last
        last, cur = cur, p
    return last


def format_name(g: Graph, x: str) -> str:
    """A readable name: the path for files and directories, path#Name for classes (and
    the other symbols)."""
    if not g.has(x):
        return x
    kind, a = g.kind(x), g.attrs(x)
    if kind in ("file", "directory"):
        return a.get("path") or g.name(x)
    if kind in SYMBOL_KINDS:
        return f"{a.get('path')}#{g.name(x)}"
    return g.name(x)


def find_role(g: Graph, x: str) -> str:
    """The role of the file or directory a node belongs to (source, test, docs, …); nodes
    outside the file tree (services, endpoints, packages) count as source."""
    cur: str | None = x
    while cur is not None:
        if g.has(cur) and g.kind(cur) in ("file", "directory") and g.attrs(cur).get("role"):
            r = g.attrs(cur)["role"]
            return "source" if r == "mixed" else r
        cur = g.parent(cur)
    return "source"


def list_ancestors_to_unfold(g: Graph, x: str) -> list[str]:
    """The boxes to unfold so that x itself is drawn."""
    return list(g.ancestors(x))[::-1]


def list_changes(scene: Scene) -> list[dict]:
    """The changes as a numbered list, in a stable order: components added or removed (a new
    module once, with its classes, not once per class), then dependencies between modules,
    then edited files. Each item names the nodes and facts it is about, so a verdict on it can
    be shown on whatever box or edge contains them."""
    return scene.memo(_list_changes)


def _list_changes(scene: Scene) -> list[dict]:
    g = Graph(scene)
    links = Links(scene)
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
        classes = [g.name(c) for c in inner if g.kind(c) in SYMBOL_KINDS] if kind not in SYMBOL_KINDS else []
        lines = d.get("added", 0) if st == "added" else d.get("removed", 0)
        if kind == "directory":
            lines = sum(g.diff(c).get("added" if st == "added" else "removed", 0) for c in inner if g.kind(c) == "file")
        what = {"file": "module", "directory": "package", "package": "external package"}.get(kind, kind)
        title = f"{'new' if st == 'added' else 'removed'} {what} {format_name(g, x)}"
        if d.get("old_path") or d.get("renamed_to"):
            title += f" (renamed {'from ' + d['old_path'] if d.get('old_path') else 'to ' + d['renamed_to']})"
        a = g.attrs(x)
        path = a.get("path")
        comps.append({"category": "component", "status": st, "title": title, "area": find_area(g, x), "role": find_role(g, x),
                      "classes": classes, "lines": lines, "entities": [x] + inner, "facts": [],
                      "reveal": list_ancestors_to_unfold(g, x), "focus": [x],
                      "link": (links.diff(path) if kind == "file" else links.blob(path, "base" if st == "removed" else "head",
                                                                                a.get("line"), a.get("end_line")))
                      if path else None})
    groups: dict[tuple, list] = defaultdict(list)
    totals: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])  # all references between the two modules, before and after
    for r in g.relations:
        a, b = find_module(g, r.src), find_module(g, r.dst)
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
        changes = sorted((c for r in rs for c in describe_fact(scene, r.key, links)),
                         key=lambda c: ({"added": 0, "removed": 1, "changed": 2}[c["status"]], c["dst"]))
        first = changes[0]
        role = find_role(g, a)
        deps.append({"category": "dependency", "status": st, "title": f"{format_name(g, a)} → {format_name(g, b)}: {word}",
                     "area": find_area(g, a), "role": role if role != "source" else find_role(g, b),
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
                        "area": find_area(g, x), "role": find_role(g, x), "added": d.get("added", 0), "removed": d.get("removed", 0),
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
