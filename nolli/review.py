"""Reviewing a diff model (``nolli diff``): links, change totals per box, details for one
box, and where the default view should unfold first.

First version, written together with the viewer's review mode; everything here is a
guess at what a reviewer needs, to be revised once it has been used on real PRs.
"""

from __future__ import annotations

import hashlib
import re
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


TARGET = re.compile(r"^\s*(n_\w+)\s*(?:-+>|-\.->|==>)?\s*(n_\w+)?\s*$")


def read_verdicts(text: str) -> list[dict]:
    """verdicts.json: a list (or {"verdicts": [...]}) of {target, verdict, reason}. target is a
    node id of the review diagram (review.mmd), or an edge written ``source --> target`` with
    two node ids; verdict one of fine / question / problem. Numbered V1, V2, … in file order;
    raises ValueError on a bad file."""
    import json

    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("verdicts", [])
    if not isinstance(data, list):
        raise ValueError("expected a list of {target, verdict, reason}")
    out = []
    for i, v in enumerate(data, 1):
        if not isinstance(v, dict) or not isinstance(v.get("target"), str) or not TARGET.match(v["target"]) or v.get("verdict") not in VERDICTS:
            raise ValueError(f"bad verdict {v!r}: needs a target (a diagram node id, or 'n_a --> n_b') and a verdict in {VERDICTS}")
        out.append({"id": f"V{i}", "target": v["target"].strip(), "verdict": v["verdict"], "reason": str(v.get("reason", ""))})
    return out


def diagram_ids(m: Model) -> dict[str, str]:
    """The review diagram's node ids back to what they stand for: entities, and a module's
    own code (body:<file>) or a directory's."""
    from .mermaid import nid

    out = {}
    for x, e in m.entities.items():
        out[nid(x)] = x
        if e.kind in ("file", "directory"):
            out[nid(f"body:{x}")] = f"body:{x}"
    return out


def resolve_verdicts(m: Model, verdicts: list[dict]) -> list[dict]:
    """Each verdict with what it is about: ``ends`` (one entity for a box, source and target for
    an edge), ``reveal`` (what to unfold to see them) and ``label`` (how the diagram names it).
    A target the diagram never had keeps ``ends`` empty."""
    from .labels import is_body

    ids = diagram_ids(m)
    out = []
    for v in verdicts:
        g = TARGET.match(v["target"])
        ends = [ids[t] for t in g.groups() if t] if g and all(t in ids for t in g.groups() if t) else []
        plain = lambda x: x[5:] if is_body(x) else x  # noqa: E731
        name = lambda x: short(m, plain(x)) + (" (own code)" if is_body(x) else "")  # noqa: E731
        reveal: list[str] = []
        for x in ends:
            for a in ancestors_to_unfold(m, plain(x)) + ([plain(x)] if is_body(x) else []):
                if a not in reveal:
                    reveal.append(a)
        out.append(v | {"ends": ends, "edge": len(ends) == 2, "reveal": reveal,
                        "label": " → ".join(name(x) for x in ends) if ends else v["target"]})
    return out


def review_expanded(m: Model) -> list[str]:
    """The boxes to unfold so that every change in source code is drawn: what going to each
    of them unfolds, together. The review diagram and the viewer's "all changes" use it."""
    out = {"fs:."}
    for it in change_items(m):
        if it["role"] == "source":
            out.update(it["reveal"])
    return sorted(out)


def review_markdown(m: Model, mermaid: str, description: str | None = None) -> str:
    """What a review agent gets: the change's title, the diagram of everything that changed
    (the reviewer looks at the same picture), how to answer, and the PR's description.
    Verdicts come back as JSON on the diagram's nodes and edges and are shown in the viewer."""
    d = m.meta["diff"]
    links = Links(m)
    head = f"# Architecture review: {d.get('title') or 'change'}\n\n"
    head += (f"{d['base_short']} → {d['head_short']}" + (f", PR #{d['pr']}" if d.get("pr") else "")
             + f": {d['files_changed']} files, +{d['added']} −{d['removed']} lines"
             + (f"\n{links.change()}" if links.change() else "") + "\n")
    howto = """
## What to do

The diagram below is the change: the repository with every area that changed in source code
unfolded, everything else folded. Boxes and edges carry the classes `added`, `removed` or
`changed`; an edge's label says how its number of references changed (`added 0→3`). A
reviewer looks at this same picture in a viewer, and your verdicts are shown on it: each
one as a mark on one box or edge. Clicking the mark shows your reason on its own, not next
to your other verdicts and not next to the code. The reviewer has seen the diagram and the
title of the change, nothing else.

Review the way they do:

1. **The diagram first, with only the title.** For each added, removed or changed box and
   edge: how expected is it, given what the change is for? Does it make a structure of the
   graph better or worse: create or break a cycle, depend upwards across layers (a
   lower-level module on a higher-level one), add a hub or a fan-out, cross between areas
   that were separate, leave something orphaned or duplicated?
2. **Then the reasons, where they help.** Read the description below and, if needed, the
   code, to find out why a box or edge was added or removed.

Give every changed box and edge a verdict:

- `fine`: expected for what the change is for, or surprising but justified by the reason
  you found;
- `question`: surprising, or it makes the structure worse, and you found no convincing
  reason; something to ask the author;
- `problem`: it makes the structure worse, and the reason you found does not justify it.

Leave a change out only when another verdict already covers it (the classes of a new
module, when you judge the module).

Judge "expected" by the title literally: a change that is what the title describes (a new
dependency on the thing the title says calls now go through) is expected. Say so in a few
words; do not make it sound surprising.

Each reason is about the box or edge it is on. It starts with a headline of one to four
words and a colon that sums up the whole verdict, the point a reviewer takes away without
reading on: "As intended:", "New cycle:", "Upward dependency:", "Redundant import:",
"Domain rule in events/:". Then at most three short sentences, in this order: how expected
the change is, given the title; what it does to the structure of the graph, in terms of the
diagram's boxes; and only if it adds something, the actual reason, phrased so that it needs
no knowledge of the code. Name boxes as the diagram labels them, never by their node ids. A
concern about a different box or edge goes on that one.

Answer with JSON only, saved as `verdicts.json` next to this file. `target` is a node id
from the diagram (`n_fs_src_app_py`), or an edge as `source --> target` with the two node
ids of an edge drawn in the diagram:

```json
[{"target": "n_fs_src_app_py --> n_fs_src_db_py", "verdict": "question", "reason": "…"}]
```
"""
    picture = "\n## The diagram\n\n```mermaid\n" + mermaid + "```\n"
    if description:  # its headings go two levels down, under this one
        description = re.sub(r"^(#{1,4}) ", r"##\1 ", description.strip(), flags=re.M)
    desc = f"\n## The description\n\nRead this after the diagram (step 2).\n\n{description}\n" if description else ""
    return head + howto + picture + desc


# ---------- what a box's or edge's hover card says about the change ----------

def _verdict_lines(verdicts: list[dict]) -> list[list]:
    """One line per verdict, worst first: [!] reason."""
    return [[{"verdict": v["verdict"]}, " ", v["reason"]] for v in sorted(verdicts, key=lambda v: -VERDICT_RANK[v["verdict"]])]


def node_review_info(nd: dict | None, verdicts: list[dict]) -> list[list]:
    """Sentences on a box's hover card in a review: what happened to it, and the verdicts on it."""
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
    return lines + _verdict_lines(verdicts)


def edge_review_info(m: Model, ed: dict | None, verdicts: list[dict]) -> list[list]:
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
    return lines + _verdict_lines(verdicts)
