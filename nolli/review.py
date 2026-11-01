"""The review of a diff model: the brief for a review agent (``review.md``, around the
diagram of every change), the agent's verdicts on that diagram's nodes and edges and how
they map back to the model, and what a box's or edge's hover card says about the change.
Built on ``nolli.changes``.

First version; the brief's wording was tuned on one PR.
"""

from __future__ import annotations

import re
from collections import Counter

from . import address
from .changes import Links, ancestors_to_unfold, change_items, short
from .mermaid import nid
from .model import Model
from .prose import code, count, listing, mark, one_line, paragraphs, say

# least attention first: praise asks for more than "fine", a doubt more than praise (the viewer
# shows the mark of a box's highest-ranked verdict). The brief offers fine / good / question / problem;
# brilliant and blunder are not offered yet.
VERDICTS = ("fine", "good", "brilliant", "question", "problem", "blunder")
VERDICT_RANK = {v: i for i, v in enumerate(VERDICTS)}


TARGET = re.compile(r"^\s*(n_\w+)\s*(?:-+>|-\.->|==>)?\s*(n_\w+)?\s*$")


def read_verdicts(text: str) -> list[dict]:
    """verdicts.json: a list (or {"verdicts": [...]}) of {target, verdict, reason}. target is a
    node id of the review diagram (review.mmd), or an edge written ``source --> target`` with
    two node ids; verdict one of VERDICTS. Numbered V1, V2, … in file order;
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
    """The review diagram's node ids back to what they stand for: entities (an unfolded one is a
    subgraph, ``<id>_g``), and a module's own code (``<file>#``) or a directory's."""
    out = {}
    for x, e in m.entities.items():
        out[nid(x)] = out[nid(x) + "_g"] = x
        if e.kind in ("file", "directory"):
            out[nid(address.own_code(x))] = address.own_code(x)
    return out


def resolve_verdicts(m: Model, verdicts: list[dict]) -> list[dict]:
    """Each verdict with what it is about: ``ends`` (one entity for a box, source and target for
    an edge), ``reveal`` (what to unfold to see them) and ``label`` (how the diagram names it).
    A target the diagram never had keeps ``ends`` empty."""
    ids = diagram_ids(m)
    out = []
    for v in verdicts:
        g = TARGET.match(v["target"])
        ends = [ids[t] for t in g.groups() if t] if g and all(t in ids for t in g.groups() if t) else []
        name = lambda x: short(m, address.owner(x)) + (" (own code)" if address.is_own_code(x) else "")  # noqa: E731
        reveal: list[str] = []
        for x in ends:
            for a in ancestors_to_unfold(m, address.owner(x)) + ([address.owner(x)] if address.is_own_code(x) else []):
                if a not in reveal:
                    reveal.append(a)
        out.append(v | {"ends": ends, "edge": len(ends) == 2, "reveal": reveal,
                        "label": " → ".join(name(x) for x in ends) if ends else v["target"]})
    return out


EDGE = re.compile(r"^\s*(?P<src>n_\w+)\s*(?:-->|-\.->|==>)(?:\|(?P<label>[^|]*)\|)?\s*(?P<dst>n_\w+)\s*$")
NODE = re.compile(r"^\s*(?:subgraph\s+)?(n_\w+)\s*[\[(]")
NODE_CLASS = re.compile(r"^\s*(?:subgraph\s+)?(n_\w+)\b.*:::(\w+)\s*$")  # a node drawn with its class
CLASS = re.compile(r"^\s*class\s+(n_\w+)\s+(\w+)\s*$")  # a class given to a node (or subgraph) afterwards
CHANGED = ("added", "removed", "changed")


def diagram_changes(mermaid: str) -> dict[str, str]:
    """What the review diagram (review.mmd) draws as changed: a node id (a subgraph's without
    ``_g``), or ``"<src> <dst>"`` for an edge, to added, removed or changed. What it draws
    unchanged is missing."""
    out = {}
    for line in mermaid.splitlines():
        if g := EDGE.match(line):
            word = (g["label"] or "").split(" ")[0]
            if word in CHANGED:
                out[f"{g['src']} {g['dst']}"] = word
        elif (g := NODE_CLASS.match(line) or CLASS.match(line)) and g.group(2) in CHANGED:
            out[g.group(1).removesuffix("_g")] = g.group(2)
    return out


def check_verdicts(m: Model, mermaid: str, text: str) -> tuple[list[dict], list[str]]:
    """verdicts.json checked against the review diagram it answers (review.mmd): the verdicts
    read, and what is wrong with them, each problem a sentence an agent can act on. Stricter
    than ``read_verdicts``: every target must be a box or an edge drawn in that diagram, and
    every verdict needs a reason."""
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"not valid JSON: {exc}"]
    if isinstance(data, dict):
        data = data.get("verdicts")
    if not isinstance(data, list):
        return [], ['expected a JSON list of {"target", "verdict", "reason"} (or {"verdicts": [...]})']
    ids = diagram_ids(m)
    nodes = {g.group(1) for line in mermaid.splitlines() if (g := NODE.match(line))}
    edges = {(ids.get(g["src"]), ids.get(g["dst"])) for line in mermaid.splitlines() if (g := EDGE.match(line))}
    errors, ok = [], []
    for i, v in enumerate(data, 1):
        where = f"verdict {i}"
        if not isinstance(v, dict):
            errors.append(f"{where}: not an object")
            continue
        target, verdict, reason = v.get("target"), v.get("verdict"), v.get("reason")
        where = f"verdict {i} ({target!r})"
        if verdict not in VERDICTS:
            errors.append(f"{where}: verdict is {verdict!r}, must be one of {', '.join(VERDICTS)}")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{where}: needs a reason (a non-empty string)")
        g = TARGET.match(target) if isinstance(target, str) else None
        if not g:
            errors.append(f"{where}: target must be a node id of the diagram (n_…) or an edge 'n_a --> n_b'")
            continue
        missing = [t for t in g.groups() if t and t not in nodes]
        if missing:
            errors.append(f"{where}: {', '.join(missing)} is not a node of the diagram")
            continue
        if g.group(2):
            a, b = ids[g.group(1)], ids[g.group(2)]
            if (a, b) not in edges:
                hint = " (the diagram has it the other way round)" if (b, a) in edges else ""
                errors.append(f"{where}: the diagram draws no edge from {g.group(1)} to {g.group(2)}{hint}")
                continue
        ok.append(v)
    if not data:
        errors.append("no verdicts: give at least one")
    return (read_verdicts(json.dumps(ok)) if ok else []), errors


def review_expanded(m: Model) -> list[str]:
    """The boxes to unfold so that every change in source code is drawn: what going to each
    of them unfolds, together. The review diagram and the viewer's "all changes" use it."""
    out = {address.REPO_ROOT}
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
to your other verdicts and not next to the code. Before the diagram, the reviewer has seen
only the heading above: what the change is for.

Review the way they do:

1. **The diagram first, knowing only what the change is for.** For each added, removed or
   changed box and edge: how expected is it, given what the change is for? Does it make a
   structure of the graph better or worse: create or break a cycle, depend upwards across
   layers (a lower-level module on a higher-level one), add a hub or a fan-out, cross
   between areas that were separate, leave something orphaned or duplicated?
2. **Then the reasons, where they help.** Read the description below and, if needed, the
   code, to find out why a box or edge was added or removed.

Give every changed box and edge a verdict:

- `fine`: expected for what the change is for, or surprising but justified by the reason
  you found;
- `good`: it makes the structure clearly better: it breaks a cycle, removes an upward
  dependency or a duplicate, or separates areas that were tangled. Not for a change that
  is only as intended or tidy; most changes have none or a few of these;
- `question`: surprising, or it makes the structure worse, and you found no convincing
  reason; something to ask the author;
- `problem`: it makes the structure worse, and the reason you found does not justify it.

Leave a change out only when another verdict already covers it (the classes of a new
module, when you judge the module).

Judge "expected" literally by what the change says it is for: a change that does just that
(a new dependency on the thing the change says calls now go through) is expected. Say so in
a few words; do not make it sound surprising.

Each reason is about the box or edge it is on, and is read on its own, without the heading
or the description: call the change "this change", never "the title" or "the description".
It starts with a headline of one to four words and a colon that sums up the whole verdict,
the point a reviewer takes away without reading on.

- A `fine` reason is "As intended:" and one short sentence: what the box or edge is in this
  change, and, if it says something, why it sits where it does. "As intended: the cache this
  change puts in front of the store. Next to `store`, where its callers already are." Not
  how its code works.
- Any other verdict names its point in the headline: "Cycle broken:", "Upward dependency
  removed:", "New cycle:", "Upward dependency:", "Redundant import:", "Domain rule in
  events/:". Then at most three short sentences, in this order: how expected the change is;
  what it does to the structure of the graph, in terms of the diagram's boxes; and only if
  it adds something, the actual reason, phrased so that it needs no knowledge of the code.

Name boxes as the diagram labels them, never by their node ids. A concern about a different
box or edge goes on that one.

Write every name in backticks, as Markdown code: boxes (`events/service`), modules,
classes, functions and other symbols (`PuppetGateway`, `character_conv_id`). The viewer
sets them apart from the prose, the way it shows names everywhere else.

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

FULL_VERDICTS = 3  # a card lists this many verdicts in full; beyond, it sums up those from inside


def _verdict_lines(verdicts: list[dict], inside: list[dict] = (), items: int = 0, where: str = "") -> list[str]:
    """One line per verdict, worst first: [!] reason. Verdicts rolled up from inside a folded box
    or bundled edge (``inside``) become one line that counts them like the review panel does,
    "15 changes in this directory · verdicts: [??] 1 [✓] 8", once there are more than a card
    lists (FULL_VERDICTS in all) and more than one of them."""
    if len(verdicts) + len(inside) <= FULL_VERDICTS or len(inside) < 2:
        verdicts, inside = [*verdicts, *inside], []
    lines = [mark(v["verdict"]) + " " + one_line(v["reason"]) for v in sorted(verdicts, key=lambda v: -VERDICT_RANK[v["verdict"]])]
    if inside:
        n = Counter(v["verdict"] for v in inside)
        line = say(f"{{}} {where} · verdicts:", count(items, "change")) if items else f"Verdicts {where}:"
        for v in sorted(n, key=lambda v: -VERDICT_RANK[v]):
            line += " " + mark(v) + " " + code(n[v])
        lines.insert(0, line)
    return lines


def node_review_info(nd: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0, kind: str = "box") -> str:
    """Sentences on a box's hover card in a review: what happened to it, the verdicts on it, and
    those on what it holds (``inside``, summed up when there are several; ``items`` changes), as Markdown."""
    lines: list[str] = []
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
    return paragraphs(lines + _verdict_lines(verdicts, inside, items, f"in this {kind}"))


def edge_review_info(m: Model, ed: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0) -> str:
    """Sentences on top of an edge's hover card in a review: how its references changed, which
    names came or went, and the verdicts on it and on the dependencies it bundles (``inside``), as Markdown."""
    lines: list[str] = []
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
    return paragraphs(lines + _verdict_lines(verdicts, inside, items, "on this edge"))
