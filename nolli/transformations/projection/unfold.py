"""Chooses which containers to unfold so a projection fits a node budget.
"""

from __future__ import annotations

from collections import defaultdict

from nolli.model.scene import Scene
from nolli.model.schema import EntityId
from nolli.transformations.projection import rollup
from nolli.transformations.projection.reading import Reading


def _unfold_cost(g: Reading, v: str, hidden: set[str], anchors: set[str] = frozenset()) -> int:
    """Nodes added by unfolding v, minus v itself. A module adds its anchor; a package's
    ``__init__`` is no node but contributes its classes and, if it has edges, an anchor."""
    kids = [c for c in g.children(v) if c not in hidden]
    if g.kind(v) == "file":
        return len(kids)
    n = 0
    for c in kids:
        if rollup.is_init(g, c):
            n += sum(1 for cc in g.children(c) if cc not in hidden)
        else:
            n += 1
    return n + (1 if v in anchors else 0) - 1


def find_default_cut(g: Reading, budget: int = 34, hidden_roles=None, always_expand=(EntityId.REPO_ROOT,), hidden: set[str] | None = None) -> dict:
    """The default cut: unfold the containment forest as far as a node budget allows.

    Start with the repository root unfolded (its top-level entries) plus the synthetic
    groups folded. Then repeatedly unfold the container that reveals the most edges per node
    it costs (relations between its children, divided by the children it adds), as long as
    the result stays within the budget. Unfolding shows all of a container's children;
    there is no partial opening. Groups with no internal structure (externals, env,
    endpoints) never unfold by themselves.

    In a change the budget goes to the changes first (``_score_changes``), then whatever is
    left to structure as usual."""

    roles = tuple(hidden_roles) if hidden_roles is not None else rollup.hidden_by_role.__defaults__[0]
    if hidden is None:
        hidden = rollup.hidden_by_role(g, roles) | set(rollup.image_aliases(g))
    lca, _loose = rollup.reveal_scores(g, hidden)
    anchors = rollup.init_anchor_dirs(g, hidden)
    expanded: set[str] = {e for e in always_expand if g.has(e)}
    diff = g.is_change
    # in a review, boxes where nothing changed are faded and cost half a box of attention
    changed = _index_changes(g) if diff else {}
    phases = [_score_changes(g, hidden, changed), lca] if diff else [lca]
    weight = (lambda x: 1.0 if x in changed or x.is_own_code else 0.5) if diff else (lambda x: 1.0)

    def cost_of_unfolding(v: str) -> float:
        if not diff:
            return _unfold_cost(g, v, hidden, anchors)
        after = rollup.visible_from_expanded(g, expanded | {v}, hidden, anchors=anchors)
        return sum(weight(x) for x in after) - sum(weight(x) for x in visible)

    for scores in phases:
        while True:
            visible = rollup.visible_from_expanded(g, expanded, hidden, anchors=anchors)
            room = budget - sum(weight(x) for x in visible)
            best, best_key = None, (0.0, 0.0)
            for v in visible:
                if v.is_own_code or g.kind(v) not in ("directory", "group", "file") or not g.children(v):
                    continue
                if scores.get(v, 0) <= 0:
                    continue
                score, delta = scores.get(v, 0), cost_of_unfolding(v)
                if score <= 0 or delta <= 0 or delta > room:
                    continue
                key = (score / delta, score)
                if key > best_key or (key == best_key and best is not None and v < best):
                    best, best_key = v, key
            if best is None:
                break
            expanded.add(best)
    visible = rollup.visible_from_expanded(g, expanded, hidden, anchors=anchors)
    return {"budget": budget, "expanded": sorted(expanded), "visible": sorted(visible), "hidden_roles": list(roles)}


# ---------- unfolding towards a change ----------

def _index_changes(g: Reading) -> dict[str, dict]:
    """Per node of a change, the changes in its subtree (itself included): changed lines and
    files (from file records, so class lines are not counted twice), nodes added and removed."""
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


def _score_changes(g: Reading, hidden: set[str], idx: dict[str, dict]) -> dict[str, float]:
    """Per container, how much unfolding it shows about the change: changed relations that
    run between its children (weighted), plus children that contain changes
    (``idx``: ``_index_changes``)."""
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
