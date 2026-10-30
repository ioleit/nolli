"""The default cut: unfold the containment forest as far as a node budget allows.

Start with the repository root unfolded (its top-level entries) plus the
synthetic groups folded. Then repeatedly unfold the container that reveals the
most edges per node it costs (relations between its children, divided by the
children it adds), as long as the result stays within the budget. Unfolding
shows all of a container's children; there is no partial opening. Groups with
no internal structure (externals, env, endpoints) never unfold by themselves.
"""

from __future__ import annotations

from .rollup import hidden_by_role, image_aliases, init_anchor_dirs, is_init, reveal_scores, visible_from_expanded
from .model import Model


def unfold_cost(m: Model, v: str, hidden: set[str], anchors: set[str] = frozenset()) -> int:
    """Nodes added by unfolding v, minus v itself. A module adds its anchor; a package's
    ``__init__`` is no node but contributes its classes and, if it has edges, an anchor."""
    kids = [c for c in m.children(v) if c not in hidden]
    if m.entities[v].kind == "file":
        return len(kids)
    n = 0
    for c in kids:
        if is_init(m, c):
            n += sum(1 for cc in m.children(c) if cc not in hidden)
        else:
            n += 1
    return n + (1 if v in anchors else 0) - 1


def default_view(m: Model, budget: int = 34, hidden_roles=None, always_expand=("fs:.",)) -> dict:
    roles = tuple(hidden_roles) if hidden_roles is not None else hidden_by_role.__defaults__[0]
    hidden = hidden_by_role(m, roles) | set(image_aliases(m))
    lca, _loose = reveal_scores(m, hidden)
    anchors = init_anchor_dirs(m, hidden)
    expanded: set[str] = {e for e in always_expand if m.has(e)}
    while True:
        visible = visible_from_expanded(m, expanded, hidden)
        room = budget - len(visible)
        best, best_key = None, (0.0, 0)
        for v in visible:
            if v.startswith("body:") or m.entities[v].kind not in ("directory", "group", "file") or not m.children(v):
                continue
            score, delta = lca.get(v, 0), unfold_cost(m, v, hidden, anchors)
            if score <= 0 or delta < 1 or delta > room:
                continue
            key = (score / delta, score)
            if key > best_key or (key == best_key and best is not None and v < best):
                best, best_key = v, key
        if best is None:
            break
        expanded.add(best)
    visible = visible_from_expanded(m, expanded, hidden)
    return {"budget": budget, "expanded": sorted(expanded), "visible": sorted(visible), "hidden_roles": list(roles)}
