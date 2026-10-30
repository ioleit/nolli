"""The scene: everything the viewer draws for one view state, computed in one place.

A view state is the set of unfolded containers plus the display options. ``scene``
turns it into a flat list of nodes (in drawing order, each naming its drawn parent) and
a list of edges, with labels, badges, emphasis flags and hover descriptions (info.py) already decided. The
viewer only lays this out (ELK) and draws it; ``nolli extract`` writes the scene of the
default cut. Ids are stable across states (entity ids, ``body:<id>`` for a module's own
code, ``kind|src|dst|subkind`` for edges), so a client can diff two scenes to animate
the step between them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field

from .model import USE, Model
from .rollup import (
    HIDDEN_ROLES_DEFAULT, hidden_by_role, RolledUp, bundle, chain_end, display_path, drawn_parent, image_aliases, is_init, owner_of,
    representative, rollup, utility_badges, visible_from_expanded,
)
from .changes import Links, change_items, diff_index, fact_change, is_diff, node_diff
from .review import VERDICT_RANK, edge_review_info, node_review_info, resolve_verdicts
from .view import default_view
from .labels import MODULE_LANGS, is_body, label_of, leaf_count
from .info import Dependencies, edge_info, node_info
from .prose import Ctx

ALL_ROLES = ("source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other")
DEFAULT_ROLES = tuple(r for r in ALL_ROLES if r not in HIDDEN_ROLES_DEFAULT)
DEFAULT_BUDGET = 34
DEFAULT_UTILITY_IN = 6  # more incoming edges than this: a utility, drawn as badges
DEFAULT_GROUPS = ("http",)  # root groups drawn as boxes; the others are badges on their users


@dataclass
class ViewState:
    """What the viewer asks for. ``expanded=None`` means: the default cut for ``budget``."""
    expanded: list[str] | None = None
    roles: list[str] = field(default_factory=lambda: list(DEFAULT_ROLES))  # shown roles
    budget: int = DEFAULT_BUDGET
    groups: list[str] = field(default_factory=lambda: list(DEFAULT_GROUPS))  # shown root groups
    through: bool = True  # derive caller -> handler through interfaces
    dangling_use: bool = False  # show reads of env variables nobody sets
    env_edges: bool = False  # draw env wiring (reader -> setter)
    bundle: bool = True  # fan-out edges to a container's boundary
    utilities: int = DEFAULT_UTILITY_IN  # boxes with more incoming edges are badges on their users; 0: off
    hide_internal: bool = False  # drop edges inside a container, count them in its header
    side: str = "both"  # diff models: "base" or "head" draws one commit, "both" the merged picture


def hidden_for(m: Model, roles, side: str = "both") -> set[str]:
    """Files and directories whose role is not shown, plus images drawn as their service; in a
    diff model drawn for one side, what does not exist on that side."""
    shown = set(roles)
    out = hidden_by_role(m, {e.attrs["role"] for e in m.entities.values() if e.attrs.get("role")} - shown)
    if side in ("base", "head"):
        gone = "added" if side == "base" else "removed"
        out |= {e.id for e in m.entities.values() if (e.attrs.get("diff") or {}).get("status") == gone}
    return out | set(image_aliases(m))


def is_root_group(m: Model, x: str) -> bool:
    return m.has(x) and m.entities[x].kind == "group" and m.parent(x) is None


def compute(m: Model, st: ViewState) -> tuple[set[str], set[str], set[str], RolledUp, set[str]]:
    """(expanded, hidden, visible, rolled, utilities) for a state; expanded is normalised to
    the containers actually reached, so folding a box also folds everything inside it."""
    hidden = hidden_for(m, st.roles, st.side)
    shown_groups = set(st.groups)
    if st.expanded is None:
        hidden_roles = [r for r in ALL_ROLES if r not in set(st.roles)]
        expanded = set(default_view(m, budget=st.budget, hidden_roles=hidden_roles, hidden=hidden)["expanded"])
    else:
        expanded = {x for x in st.expanded if m.has(x)}
    expanded = {x for x in expanded if not (is_root_group(m, x) and x not in shown_groups)}  # filtered out: stays folded
    walked: set[str] = set()
    visible = visible_from_expanded(m, expanded, hidden, walked=walked)
    expanded = walked
    rolled = rollup(m, visible, hidden, through_interfaces=st.through,
                    dangling_use_kinds=("endpoint", "env") if st.dangling_use else (), env_edges=st.env_edges,
                    shown_groups=shown_groups, side=st.side)
    if st.bundle:
        rolled.edges = bundle(m, rolled.edges, expanded)
    utilities = utility_badges(m, rolled, st.utilities) if st.utilities > 0 else set()
    return expanded, hidden, visible, rolled, utilities


# ---------- labels ----------

def icon_of(m: Model, x: str) -> str:
    if is_body(x):
        return ""
    e = m.entities[x]
    if e.kind == "directory":
        return "D"
    if e.kind == "file" and e.attrs.get("language") in MODULE_LANGS:
        return "m"
    if e.kind == "class":
        return "C"
    return ""


def badge_text(used_by: int = 0) -> str:
    """For a utility, how many boxes use it. What a box itself depends on outside the
    picture (external packages, environment, deployment, utilities) is not drawn on it:
    the hover text says it (info.py)."""
    return f"used by {used_by}" if used_by else ""


def edge_label(e) -> str:
    s = e.subkind.removesuffix("-dangling")
    parts = [str(e.count)] if e.count > 1 else []
    if e.kind == "provide":
        parts.append("realised by")
    elif s != "import":
        parts.append("http" if s == "endpoint" else s)
    if e.subkind.endswith("-dangling"):
        parts.append("unbound")
    return " ".join(parts)


def edge_style(e) -> str:
    s = e.subkind.removesuffix("-dangling")
    if e.kind == "provide":
        return "provide"
    if s == "import":
        return "import"
    if s in ("http", "endpoint"):
        return "http"
    if s == "env":
        return "env"
    return "deploy"




# ---------- the scene ----------

def scene(m: Model, st: ViewState, verdicts: list[dict] | None = None) -> dict:
    """``verdicts``: a review's verdicts (review.read_verdicts), shown on the drawn box or
    edge that stands for each one's target."""
    expanded, hidden, visible, rolled, utilities = compute(m, st)
    diff = is_diff(m)
    links = Links(m) if diff else None
    by_fact = {f"{r.kind}|{r.src}|{r.dst}|{r.subkind}": r for r in m.relations if r.attrs.get("diff")} if diff else {}
    shown_groups = set(st.groups)
    # a root group is drawn when the filter shows it and it has something to unfold; a group
    # the filter hides is drawn only if an edge still ends on it (both ends in groups)
    root_group = lambda x: is_root_group(m, x) and (x not in shown_groups or not m.children(x))  # noqa: E731

    def local(e) -> bool:
        ps = drawn_parent(m, e.src, expanded)
        return ps is not None and ps == drawn_parent(m, e.dst, expanded)

    internal_drawn: dict[str, int] = defaultdict(int)  # local edges per container
    for e in rolled.edges:
        if local(e):
            internal_drawn[drawn_parent(m, e.src, expanded)] += e.count
    edges = [e for e in rolled.edges if not (st.hide_internal and local(e))]
    out_total: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.kind == USE:
            out_total[e.src] += e.count
    touched = {e.src for e in edges} | {e.dst for e in edges}
    drawn = sorted(v for v in visible if not (root_group(v) and v not in touched))

    used_by: dict[str, int] = defaultdict(int)  # utility -> boxes whose edges into it became badges
    for ann in rolled.annotations.values():
        for g in ann:
            if g.startswith("util:"):
                used_by[g[5:]] += 1
    services = [c for c in m.children("deploy") if m.entities[c].kind == "service"] if m.has("deploy") else []
    nodes: list[dict] = []
    ctx = Ctx(m, rolled, expanded, hidden, utilities)
    deps = Dependencies(ctx, set(visible) | expanded)

    def used_utils(x: str) -> list[str]:
        return sorted(g[5:] for g in (rolled.annotations.get(x) or {}) if g.startswith("util:"))

    def node(x: str, parent: str | None, container: bool) -> dict:
        if is_body(x):
            return {"id": x, "kind": "anchor", "parent": parent, "utilities_used": used_utils(x), "info": node_info(ctx, deps, x)}
        e = m.entities[x]
        badge = badge_text(used_by.get(x, 0))
        if container and st.hide_internal and internal_drawn.get(x):
            badge = " · ".join(filter(None, [badge, f"{internal_drawn[x]} internal"]))
        kids = bool(m.children(x))
        return {
            "id": x, "kind": e.kind, "parent": parent, "container": container, "label": label_of(m, x, expanded),
            "icon": icon_of(m, x), "foldable": kids, "leaves": leaf_count(m, x) if kids and not container else None,
            "badge": badge, "utility": x in utilities,
            "chips": [{"id": s, "name": m.entities[s].name, "color": services.index(s) if s in services else -1}
                      for s in rolled.allocations.get(x, [])],
            "service_color": services.index(x) if x in services else None,
            "internal": internal_drawn.get(x, 0) if container else None,
            "uses": [],  # kept for the viewer; empty: utilities used are in the hover text (info.py)
            "utilities_used": used_utils(x),  # ids of the utilities whose edges from this box became badges
            "info": node_info(ctx, deps, x, used_by.get(x, 0)),
            "diff": node_diff(m, x, rolled.internal_changes.get(x)) if diff else None,
        }

    def build(x: str, parent: str | None) -> bool:
        e = m.entities[x]
        if x in expanded and m.children(x):
            rec = node(x, parent, True)
            if e.attrs.get("root") and x == "fs:.":
                rec = None  # the repository root is the canvas, not a box
            else:
                nodes.append(rec)
            me = None if rec is None else x
            ch: list[str] = []
            for c in m.children(x):
                if c in hidden:
                    continue
                if e.kind == "directory" and is_init(m, c):
                    ch.extend(m.children(c))  # a package's __init__ classes are its children
                    continue
                ch.append(chain_end(m, c, hidden) if e.kind == "directory" and m.entities[c].kind == "directory" else c)
            n = 0
            if f"body:{x}" in visible:
                nodes.append(node(f"body:{x}", me or parent, False))
                n += 1
            for c in ch:
                if c in visible or (c in expanded and m.children(c)):
                    n += build(c, me or parent)
            if rec is not None and n == 0:
                rec["container"] = False
            return True
        if x not in visible or (root_group(x) and x not in touched):
            return False
        nodes.append(node(x, parent, False))
        return True

    for r in m.roots():
        if r not in hidden:
            build(r, None)

    # what a box uses outside the picture (utilities among it) is in its hover text, not
    # drawn in it: "uses" stays empty, so the layout reserves no room for a list

    edge_out = []
    for e in edges:
        ot = out_total.get(e.src, 0)
        weak = bool(ot) and e.kind == USE and e.count / ot < 0.05 and e.count < 3
        edge_out.append({
            "key": f"{e.kind}|{e.src}|{e.dst}|{e.subkind}", "kind": e.kind, "subkind": e.subkind, "src": e.src, "dst": e.dst,
            "count": e.count, "confidence": e.confidence, "via": sorted(e.via), "style": edge_style(e), "label": edge_label(e),
            "local": local(e), "single": e.count == 1,
            "weak": weak,
            "dangling": e.subkind.endswith("-dangling"), "heuristic": e.confidence == "heuristic",
            "info": edge_info(ctx, e),
            "diff": ({"status": e.status, "base": e.base, "head": e.head, "facts": sorted(e.changed),
                      "changes": [fact_change(m, by_fact[f], links) for f in sorted(e.changed)[:40] if f in by_fact],
                      "more": max(0, len(e.changed) - 40)} if diff and e.status else None),
        })

    if diff:
        attach_items(m, nodes, edge_out, visible, hidden, resolve_verdicts(m, verdicts or []))

    residue = [f"{len(hidden)} entities hidden by role", f"{rolled.hidden_relations} relations touch hidden entities"]
    residue += [f"{v} unresolved {k} references" for k, v in sorted(rolled.unresolved.items())]
    dangling = sum(1 for e in edges if e.subkind.endswith("-dangling"))
    if dangling:
        residue.append(f"{dangling} interface edges with one side only")
    if utilities:
        residue.append("utilities (badges): " + ", ".join(label_of(m, u, expanded) for u in sorted(utilities)))
    residue += [f"{v} {k} wiring edges not drawn (option)" for k, v in rolled.suppressed.items()]

    groups = [{"id": r, "name": m.entities[r].name, "size": len(m.children(r)), "shown": r in shown_groups}
              for r in m.roots() if m.entities[r].kind == "group" and r != "unresolved" and m.children(r)]
    state = asdict(st) | {"expanded": sorted(expanded)}
    out = {"state": state, "nodes": nodes, "edges": edge_out, "groups": groups, "residue": residue,
           "stats": {"nodes": len(drawn), "edges": len(edge_out)}}
    if diff:
        meta = m.meta["diff"]
        statuses = defaultdict(int)
        for e in edge_out:
            if e["diff"]:
                statuses[e["diff"]["status"]] += 1
        idx = diff_index(m)
        out["diff"] = {**{k: meta.get(k) for k in ("base", "head", "base_short", "head_short", "remote", "pr", "files_changed", "added", "removed", "title")},
                       "link": links.change(), "drawn": dict(statuses), "changed_entities": len(idx)}
    return out


def attach_items(m: Model, nodes: list[dict], edges: list[dict], visible: set[str], hidden: set[str],
                 verdicts: list[dict]) -> None:
    """Which review items each drawn box and edge shows, and which verdicts (resolved, see
    review.resolve_verdicts) are on it, with the worst of them.
    An item about facts lands on the edges carrying those facts; an item about entities (or
    facts that fell inside one box) on the box that stands for them."""
    rep = representative(m, visible, hidden, image_aliases(m))
    drawn = {n["id"] for n in nodes}
    by_fact: dict[str, list[str]] = defaultdict(list)
    for it in change_items(m):
        for f in it["facts"]:
            by_fact[f].append(it["id"])
    node_items: dict[str, set[str]] = defaultdict(set)
    placed: set[str] = set()
    for e in edges:
        ids = sorted({i for f in (e["diff"] or {}).get("facts", []) for i in by_fact.get(f, [])}, key=lambda i: int(i[1:]))
        e["items"] = ids
        placed.update(ids)
    for it in change_items(m):
        if it["id"] in placed and it["category"] == "dependency":
            continue
        for x in it["focus"]:
            r = x if x in drawn else rep(x)  # an unfolded module is its box, not its own-code dot
            if r is not None and r in drawn:
                node_items[r].add(it["id"])
                break
    # verdicts: on the drawn box that stands for their target, or the drawn edge between the
    # boxes that stand for its two ends (an edge that fell inside one box lands on that box)
    def drawn_box(x: str) -> str | None:
        r = x if x in drawn else rep(x[5:] if is_body(x) else x)
        return r if r in drawn else None

    edge_at: dict[tuple[str, str], dict] = {}
    for e in edges:
        edge_at.setdefault((e["src"], e["dst"]), e)
    on_node: dict[str, list[dict]] = defaultdict(list)
    on_edge: dict[str, list[dict]] = defaultdict(list)
    for v in verdicts:
        ends = [drawn_box(x) for x in v["ends"]]
        if not ends or None in ends:
            continue
        if len(ends) == 2 and ends[0] != ends[1]:
            e = edge_at.get((ends[0], ends[1])) or edge_at.get((ends[1], ends[0]))
            if e is not None:
                on_edge[e["key"]].append(v)
                continue
        on_node[ends[0]].append(v)
    worst = lambda vs: max((v["verdict"] for v in vs), key=VERDICT_RANK.get, default=None)  # noqa: E731
    for n in nodes:
        n["items"] = sorted(node_items.get(n["id"], ()), key=lambda i: int(i[1:]))
        vs = on_node.get(n["id"], [])
        n["verdicts"] = [v["id"] for v in vs]
        n["verdict"] = worst(vs)
        n["review"] = node_review_info(n.get("diff"), vs)  # the card's second part, under a line
    for e in edges:
        vs = on_edge.get(e["key"], [])
        e["verdicts"] = [v["id"] for v in vs]
        e["verdict"] = worst(vs)
        e["review"] = edge_review_info(m, e.get("diff"), vs)


def state_from_query(q: dict[str, str]) -> ViewState:
    """Parse the viewer's URL parameters (the same keys as its location hash)."""
    st = ViewState()
    if q.get("x"):
        st.expanded = [x for x in q["x"].split(",") if x]
    if q.get("r") is not None:
        st.roles = [r for r in q["r"].split(",") if r]
    if q.get("b"):
        st.budget = int(q["b"])
    if q.get("g") is not None:
        st.groups = [g for g in q["g"].split(",") if g]
    st.through = q.get("t", "1") != "0"
    st.dangling_use = q.get("u") == "1"
    st.env_edges = q.get("ev") == "1"
    st.bundle = q.get("nb") != "1"
    st.utilities = 0 if q.get("nu") == "1" else int(q["ut"]) if q.get("ut") else DEFAULT_UTILITY_IN
    st.hide_internal = q.get("hi") == "1"
    if q.get("s") in ("base", "head"):
        st.side = q["s"]
    return st
