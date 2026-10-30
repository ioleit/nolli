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


# ---------- review items: the numbered list a reviewer (person or agent) goes through ----------

def module_of(m: Model, x: str) -> str:
    """The module (file) a fact end belongs to; anything outside files stays itself."""
    cur: str | None = x
    while cur is not None:
        if m.has(cur) and m.entities[cur].kind == "file":
            return cur
        cur = m.parent(cur)
    return x


def area_of(m: Model, x: str) -> str:
    """The top-level box an entity sits in: a top-level directory, or a group."""
    cur, last = x, x
    while cur is not None:
        p = m.parent(cur)
        if p is None or (m.entities[p].attrs.get("root")):
            return cur if p is not None else last
        last, cur = cur, p
    return last


def short(m: Model, x: str) -> str:
    """A readable name: the path for files and directories, path#Name for classes."""
    if not m.has(x):
        return x
    e = m.entities[x]
    if e.kind in ("file", "directory"):
        return e.attrs.get("path") or e.name
    if e.kind == "class":
        return f"{e.attrs.get('path')}#{e.name}"
    return e.name


def role_of(m: Model, x: str) -> str:
    """The role of the file or directory an entity belongs to (source, test, docs, …);
    entities outside the file tree (services, endpoints, packages) count as source."""
    cur: str | None = x
    while cur is not None:
        e = m.entities.get(cur)
        if e is not None and e.kind in ("file", "directory") and e.attrs.get("role"):
            return "source" if e.attrs["role"] == "mixed" else e.attrs["role"]
        cur = m.parent(cur)
    return "source"


def ancestors_to_unfold(m: Model, x: str) -> list[str]:
    """The boxes to unfold so that x itself is drawn."""
    out, cur = [], m.parent(x)
    while cur is not None:
        out.append(cur)
        cur = m.parent(cur)
    return out[::-1]


def change_items(m: Model) -> list[dict]:
    """The changes as a numbered list, in a stable order: components added or removed
    (a new module once, with its classes, not once per class), then dependencies between
    modules, then edited files. Each item names the entities and facts it is about, so a
    verdict on it can be shown on whatever box or edge contains them."""
    cached = m.__dict__.get("_change_items")
    if cached is not None:
        return cached
    links = Links(m)
    comps, deps, content = [], [], []
    # components: added / removed entities whose parent did not change the same way
    for e in sorted(m.entities.values(), key=lambda e: e.id):
        d = e.attrs.get("diff") or {}
        st = d.get("status")
        if st not in ("added", "removed") or e.kind in ("group", "unresolved"):
            continue
        p = m.parent(e.id)
        if p is not None and (m.entities[p].attrs.get("diff") or {}).get("status") == st:
            continue  # covered by its parent's item
        inner = [c for c in m.descendants(e.id) if (m.entities[c].attrs.get("diff") or {}).get("status") == st]
        classes = [m.entities[c].name for c in inner if m.entities[c].kind == "class"]
        if e.kind == "class":
            classes = []
        lines = d.get("added", 0) if st == "added" else d.get("removed", 0)
        if e.kind == "directory":
            lines = sum((m.entities[c].attrs.get("diff") or {}).get("added" if st == "added" else "removed", 0) for c in inner if m.entities[c].kind == "file")
        what = {"file": "module", "directory": "package", "class": "class", "package": "external package"}.get(e.kind, e.kind)
        title = f"{'new' if st == 'added' else 'removed'} {what} {short(m, e.id)}"
        if d.get("old_path") or d.get("renamed_to"):
            title += f" (renamed {'from ' + d['old_path'] if d.get('old_path') else 'to ' + d['renamed_to']})"
        path = e.attrs.get("path")
        comps.append({"category": "component", "status": st, "title": title, "area": area_of(m, e.id), "role": role_of(m, e.id),
                      "classes": classes, "lines": lines, "entities": [e.id] + inner, "facts": [],
                      "reveal": ancestors_to_unfold(m, e.id), "focus": [e.id],
                      "link": (links.diff(path) if e.kind == "file" else links.blob(path, "base" if st == "removed" else "head",
                                                                                  e.attrs.get("line"), e.attrs.get("end_line")))
                      if path else None})
    # dependencies: changed facts grouped by the modules at their ends
    groups: dict[tuple, list] = defaultdict(list)
    totals: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])  # all references between the two modules, before and after
    for r in m.relations:
        if r.kind == CONTAIN:
            continue
        a, b = module_of(m, r.src), module_of(m, r.dst)
        if r.kind == "provide":
            a, b = b, a  # drawn interface -> implementation
        if a == b:
            continue  # inside one module: shows as its content
        sub = "import" if r.subkind in ("ref", "import") else r.subkind
        d = r.attrs.get("diff")
        t = totals[(a, b, r.kind, sub)]
        t[0] += d.get("base", 0) if d else r.count
        t[1] += d.get("head", 0) if d else r.count
        if d:
            groups[(a, b, r.kind, sub)].append(r)
    for (a, b, kind, sub), rs in sorted(groups.items()):
        base, head = totals[(a, b, kind, sub)]
        st = "added" if base == 0 else "removed" if head == 0 else "changed"
        word = {"added": "new dependency", "removed": "dependency removed", "changed": "dependency changed"}[st]
        if kind == "provide":
            word = {"added": "now realised by", "removed": "no longer realised by", "changed": "realisation changed"}[st]
        targets = sorted({f"{'+' if r.attrs['diff']['status'] == 'added' else '−' if r.attrs['diff']['status'] == 'removed' else '~'}"
                          f"{short(m, r.dst if kind != 'provide' else r.src)}" for r in rs})
        changes = sorted((fact_change(m, r, links) for r in rs), key=lambda c: ({"added": 0, "removed": 1, "changed": 2}[c["status"]], c["dst"]))
        first = changes[0]
        deps.append({"category": "dependency", "status": st, "title": f"{short(m, a)} → {short(m, b)}: {word}",
                     "area": area_of(m, a), "role": role_of(m, a) if role_of(m, a) != "source" else role_of(m, b),
                     "kind": kind, "subkind": sub, "base": base, "head": head,
                     "targets": targets[:12], "entities": [a, b], "facts": [fact_key(r) for r in rs],
                     "reveal": sorted(set(ancestors_to_unfold(m, a)) | set(ancestors_to_unfold(m, b))), "focus": [a, b],
                     "link": first["link"], "changes": changes[:12], "more": max(0, len(changes) - 12)})
    # content: every edited file that is neither added nor removed
    for e in sorted(m.entities.values(), key=lambda e: e.id):
        d = e.attrs.get("diff") or {}
        if e.kind != "file" or "git" not in d or d.get("status") in ("added", "removed"):
            continue
        cls = [{"name": m.entities[c].name, "added": m.entities[c].attrs["diff"].get("added", 0),
                "removed": m.entities[c].attrs["diff"].get("removed", 0)}
               for c in m.children(e.id) if (m.entities[c].attrs.get("diff") or {}).get("status") == "same"
               and (m.entities[c].attrs["diff"].get("added") or m.entities[c].attrs["diff"].get("removed"))]
        path = e.attrs.get("path")
        hunks = [{"new": h["new"], "old": h["old"], "added": len(h["added"]), "removed": len(h["removed"]),
                  "link": links.diff(path, (h["added"] or [None])[0] or (h["removed"] or [None])[0], "head" if h["added"] else "base")}
                 for h in d.get("hunks", [])] if path else []
        content.append({"category": "content", "status": "changed", "title": f"edited {path} (+{d.get('added', 0)} −{d.get('removed', 0)})",
                        "area": area_of(m, e.id), "role": role_of(m, e.id), "added": d.get("added", 0), "removed": d.get("removed", 0),
                        "classes": cls, "entities": [e.id], "facts": [], "hunks": hunks[:30], "more_hunks": max(0, len(hunks) - 30),
                        "reveal": ancestors_to_unfold(m, e.id), "focus": [e.id], "link": links.diff(path) if path else None})
    # source changes first in every category, then tests, docs, config, data; numbered in that order
    items = []
    for source in (True, False):
        for group in (comps, deps, content):
            part = [it for it in group if (it["role"] == "source") == source]
            part.sort(key=lambda it: (it["role"], it["area"], it["title"]))
            items.extend(part)
    for i, it in enumerate(items, 1):
        it["id"] = f"C{i}"
    m.__dict__["_change_items"] = items
    return items


VERDICTS = ("fine", "question", "problem")
VERDICT_RANK = {v: i for i, v in enumerate(VERDICTS)}


def read_verdicts(text: str) -> dict[str, dict]:
    """verdicts.json: a list (or {"verdicts": [...]}) of {id, verdict, reason}; verdict one of
    fine / question / problem. Returns them by item id; raises ValueError on a bad file."""
    import json

    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("verdicts", [])
    if not isinstance(data, list):
        raise ValueError("expected a list of {id, verdict, reason}")
    out = {}
    for v in data:
        if not isinstance(v, dict) or not isinstance(v.get("id"), str) or v.get("verdict") not in VERDICTS:
            raise ValueError(f"bad verdict {v!r}: needs id and verdict in {VERDICTS}")
        out[v["id"]] = {"verdict": v["verdict"], "reason": str(v.get("reason", ""))}
    return out


def review_markdown(m: Model, mermaid: str) -> str:
    """What a review agent gets: the change, the picture with ids, the numbered changes, and
    how to answer. Verdicts come back as JSON and are shown in the viewer."""
    d = m.meta["diff"]
    links = Links(m)
    items = change_items(m)
    head = f"# Architecture review: {d.get('title') or 'change'}\n\n"
    head += (f"{d['base_short']} → {d['head_short']}" + (f", PR #{d['pr']}" if d.get("pr") else "")
             + f": {d['files_changed']} files, +{d['added']} −{d['removed']} lines"
             + (f"\n{links.change()}" if links.change() else "") + "\n")
    howto = """
## What to do

Judge each change below for its effect on the architecture: does the new or removed
dependency, component or edit fit how the code is organised, or does it cross a boundary,
add coupling, duplicate something, or leave something behind? Content-only edits matter
when they change what a module is responsible for.

Answer with JSON only, one entry per change you have an opinion on, and save it as
`verdicts.json` next to this file:

```json
[{"id": "C3", "verdict": "problem", "reason": "one or two sentences"}]
```

`verdict` is one of `fine`, `question`, `problem`. Use the ids exactly as listed.
"""
    picture = ("\n## The picture\n\nThe repository rolled up around the change. Classes on boxes and edges: "
               "`added`, `removed`, `changed`; box ids are entity ids with punctuation replaced.\n\n```mermaid\n" + mermaid + "```\n")
    lines = ["\n## The changes\n"]
    titles = {"component": "Components added or removed", "dependency": "Dependencies between modules", "content": "Edited files"}
    last = None
    for it in items:
        section = (it["category"], it["role"] == "source")
        if section != last:
            if it["role"] != "source" and (last is None or last[1]):
                lines.append("\n## Tests, docs, config and data\n\nThe same kinds of change outside source code; lower priority.\n")
            lines.append(f"\n### {titles[it['category']]}\n")
            last = section
        extra = []
        if it.get("classes"):
            extra.append("classes: " + ", ".join(c if isinstance(c, str) else f"{c['name']} (+{c['added']} −{c['removed']})" for c in it["classes"]))
        if it["category"] == "dependency":
            extra.append(f"{it['base']} → {it['head']} references" + (f"; changed: {', '.join(it['targets'])}" if it.get("targets") else ""))
        if it["category"] == "component" and it.get("lines"):
            extra.append(f"{it['lines']} lines")
        link = f" — {it['link']}" if it.get("link") else ""
        role = f" [{it['role']}]" if it["role"] != "source" else ""
        lines.append(f"- **{it['id']}**{role} {it['title']}{link}" + ("".join(f"\n  {x}" for x in extra)))
    return head + howto + picture + "\n".join(lines) + "\n"


# ---------- what a box's or edge's hover card says about the change ----------

def _ids(ids: list[str]) -> list:
    from .prose import listing

    return listing("Review items {}", ids, "more", limit=8)


def _verdict_lines(ids: list[str], verdicts: dict[str, dict]) -> list[list]:
    """One line per verdict on these items, worst first: [!] problem on C1: reason."""
    out = []
    for i in sorted((i for i in ids if i in verdicts), key=lambda i: (-VERDICT_RANK[verdicts[i]["verdict"]], int(i[1:])))[:6]:
        v = verdicts[i]
        out.append([{"verdict": v["verdict"]}, " ", {"v": i}, ": ", v["reason"]])
    return out


def node_review_info(nd: dict | None, ids: list[str], verdicts: dict[str, dict]) -> list[list]:
    """Sentences on top of a box's hover card in a review: what happened to it, its items and
    their verdicts."""
    from .prose import count, say

    lines: list[list] = []
    if nd:
        lines_changed = nd["added"] or nd["removed"]
        where = f" in {count(nd['files'], 'file')}" if nd["files"] > 1 else ""
        if nd["status"] == "added":
            lines.append(say("New in this change" + (", {} lines" if lines_changed else ""), *([f"+{nd['added']}"] if lines_changed else [])))
        elif nd["status"] == "removed":
            lines.append(say("Removed in this change" + (", {} lines" if lines_changed else ""), *([f"−{nd['removed']}"] if lines_changed else [])))
        elif lines_changed:
            lines.append(say("Changed: {} {} lines" + where, f"+{nd['added']}", f"−{nd['removed']}"))
        elif not (nd.get("new") or nd.get("gone")) and nd.get("internal"):
            lines.append(say("Changed: {} between what it holds", count(nd["internal"], "dependency", "dependencies")))
        if (nd.get("new") or nd.get("gone")) and nd["status"] == "changed":
            bits = ([count(nd["new"], "new part")] if nd.get("new") else []) + ([count(nd["gone"], "removed part")] if nd.get("gone") else [])
            lines.append(say("Inside: " + " and ".join("{}" for _ in bits), *bits))
        if nd.get("old_path"):
            lines.append(say("Renamed from {}", nd["old_path"]))
        if nd.get("renamed_to"):
            lines.append(say("Renamed to {}", nd["renamed_to"]))
    if ids:
        lines.append(_ids(ids))
    return lines + _verdict_lines(ids, verdicts)


def edge_review_info(m: Model, ed: dict | None, ids: list[str], verdicts: dict[str, dict]) -> list[list]:
    """Sentences on top of an edge's hover card in a review: how its references changed, and
    which names came or went."""
    from .prose import count, listing, say

    lines: list[list] = []
    if ed:
        if ed["status"] == "added":
            lines.append(say("New in this change: {}", count(ed["head"], "reference")))
        elif ed["status"] == "removed":
            lines.append(say("Removed in this change: {} gone", count(ed["base"], "reference")))
        else:
            lines.append(say("Changed: {} → {} references", ed["base"], ed["head"]))
        name = lambda x: m.entities[x].name if m.has(x) else x  # noqa: E731
        came = sorted({name(c["dst"]) for c in ed.get("changes", []) if c["status"] == "added"})
        went = sorted({name(c["dst"]) for c in ed.get("changes", []) if c["status"] == "removed"})
        if came:
            lines.append(listing("Now uses {}", came, "more", limit=4))
        if went:
            lines.append(listing("No longer uses {}", went, "more", limit=4))
    if ids:
        lines.append(_ids(ids))
    return lines + _verdict_lines(ids, verdicts)
