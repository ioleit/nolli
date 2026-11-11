"""Writes the brief a review agent gets for a change."""

from __future__ import annotations

import re

from nolli.logic.transformations.merge import Links
from nolli.model.scene import Scene


def make_brief(scene: Scene, mermaid: str, description: str | None = None) -> str:
    """Write the brief a review agent gets for a change. It holds the change's title, the diagram
    of everything that changed (the reviewer looks at the same picture), how to answer, and the
    PR's description.
    Verdicts come back as JSON on the diagram's nodes and edges and are shown in the viewer.

    ``mermaid``: the review diagram (``review.mmd``), the change with every area that changed
    in source code unfolded (``changes.find_review_cut``)."""
    d = scene.model.meta["change"]
    links = Links(d)
    head = f"# Architecture review: {d.get('title') or 'change'}\n\n"
    head += (f"{d['base_short']} → {d['head_short']}" + (f", PR #{d['pr']}" if d.get("pr") else "")
             + f": {d['files_changed']} files, +{d['added']} −{d['removed']} lines"
             + (f"\n{links.change()}" if links.change() else "") + "\n")
    howto = """
## What to do

The diagram below is the change: the repository with every area that changed in source code
unfolded, everything else folded. Boxes and edges carry the classes `added`, `removed` or
`changed`; an edge's label says how its number of references changed (`added 0→3`). A box
marked "used by N" is a utility: the edges into it are left out, and a `~~~` link (drawn
as nothing) from one box that uses it keeps it low in the picture. A
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
    diagram = "\n## The diagram\n\n```mermaid\n" + mermaid + "```\n"
    if description:  # its headings go two levels down, under this one
        description = re.sub(r"^(#{1,4}) ", r"##\1 ", description.strip(), flags=re.MULTILINE)
    desc = f"\n## The description\n\nRead this after the diagram (step 2).\n\n{description}\n" if description else ""
    return head + howto + diagram + desc
