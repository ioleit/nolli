"""The review of a diff model: the brief for a review agent (``review.md``, around the
diagram of every change), the agent's verdicts on that diagram's nodes and edges and how
they map back to the model, and what a box's or edge's hover card says about the change.
Built on ``nolli.changes``.

First version; the brief's wording was tuned on one PR.
"""

from __future__ import annotations

import re

from .changes import Links, ancestors_to_unfold, change_items, short
from .labels import is_body
from .mermaid import nid
from .model import Model
from .prose import count, listing, say

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
    """The review diagram's node ids back to what they stand for: entities (an unfolded one is a
    subgraph, ``<id>_g``), and a module's own code (body:<file>) or a directory's."""
    out = {}
    for x, e in m.entities.items():
        out[nid(x)] = out[nid(x) + "_g"] = x
        if e.kind in ("file", "directory"):
            out[nid(f"body:{x}")] = f"body:{x}"
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


EDGE = re.compile(r"^\s*(n_\w+)\s*(?:-->|-\.->|==>)(?:\|[^|]*\|)?\s*(n_\w+)\s*$")
NODE = re.compile(r"^\s*(?:subgraph\s+)?(n_\w+)\s*[\[(]")


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
    edges = {(ids.get(g.group(1)), ids.get(g.group(2))) for line in mermaid.splitlines() if (g := EDGE.match(line))}
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

def marked(text: str) -> list:
    """Text with `code` spans (Markdown backticks) as parts: the spans become values."""
    return [{"v": t} if i % 2 else t for i, t in enumerate(text.split("`")) if t]


def _verdict_lines(verdicts: list[dict]) -> list[list]:
    """One line per verdict, worst first: [!] reason."""
    return [[{"verdict": v["verdict"]}, " ", *marked(v["reason"])] for v in sorted(verdicts, key=lambda v: -VERDICT_RANK[v["verdict"]])]


def node_review_info(nd: dict | None, verdicts: list[dict]) -> list[list]:
    """Sentences on a box's hover card in a review: what happened to it, and the verdicts on it."""
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
