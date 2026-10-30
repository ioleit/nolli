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
    HIDDEN_ROLES_DEFAULT, RolledUp, bundle, chain_end, display_path, drawn_parent, image_aliases, is_init, owner_of,
    rollup, utility_badges, visible_from_expanded,
)
from .view import default_view
from .labels import MODULE_LANGS, is_body, label_of, leaf_count
from .info import edge_info, node_info

ALL_ROLES = ("source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other")
DEFAULT_ROLES = tuple(r for r in ALL_ROLES if r not in HIDDEN_ROLES_DEFAULT)
DEFAULT_BUDGET = 34
GROUP_SHORT = {"ext": "pkgs", "env": "env", "deploy": "deploy", "http": "api"}


@dataclass
class ViewState:
    """What the viewer asks for. ``expanded=None`` means: the default cut for ``budget``."""
    expanded: list[str] | None = None
    roles: list[str] = field(default_factory=lambda: list(DEFAULT_ROLES))  # shown roles
    budget: int = DEFAULT_BUDGET
    through: bool = True  # derive caller -> handler through interfaces
    dangling_use: bool = False  # show reads of env variables nobody sets
    env_edges: bool = False  # draw env wiring (reader -> setter)
    bundle: bool = True  # fan-out edges to a container's boundary
    utilities: bool = True  # widely used nodes as badges on their users
    hide_internal: bool = False  # drop edges inside a container, count them in its header


def hidden_for(m: Model, roles) -> set[str]:
    """Files and directories whose role is not shown, plus images drawn as their service."""
    shown = set(roles)
    out = {e.id for e in m.entities.values()
           if e.kind in ("file", "directory") and not e.attrs.get("root") and e.attrs.get("role") and e.attrs["role"] not in shown}
    return out | set(image_aliases(m))


def compute(m: Model, st: ViewState) -> tuple[set[str], set[str], set[str], RolledUp, set[str]]:
    """(expanded, hidden, visible, rolled, utilities) for a state; expanded is normalised to
    the containers actually reached, so folding a box also folds everything inside it."""
    hidden = hidden_for(m, st.roles)
    if st.expanded is None:
        hidden_roles = [r for r in ALL_ROLES if r not in set(st.roles)]
        expanded = set(default_view(m, budget=st.budget, hidden_roles=hidden_roles)["expanded"])
    else:
        expanded = {x for x in st.expanded if m.has(x)}
    walked: set[str] = set()
    visible = visible_from_expanded(m, expanded, hidden, walked=walked)
    expanded = walked
    rolled = rollup(m, visible, hidden, through_interfaces=st.through,
                    dangling_use_kinds=("endpoint", "env") if st.dangling_use else (), env_edges=st.env_edges)
    if st.bundle:
        rolled.edges = bundle(m, rolled.edges, expanded)
    utilities = utility_badges(m, rolled, expanded) if st.utilities else set()
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


def badge_text(m: Model, x: str, rolled: RolledUp, expanded: set[str]) -> str:
    ann = rolled.annotations.get(x)
    if not ann:
        return ""
    return " · ".join("→ " + label_of(m, g[5:], expanded) if g.startswith("util:") else f"{c} {GROUP_SHORT.get(g, g)}"
                      for g, c in sorted(ann.items()))


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


def cycle_members(nodes: list[str], edges) -> set[str]:
    """Nodes in a strongly connected component of size > 1 (use edges only)."""
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in edges:
        if e.kind == USE and e.src in adj and e.dst in adj:
            adj[e.src].append(e.dst)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: set[str] = set()
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:  # iterative Tarjan
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on.add(v)
            recurse = False
            for j in range(i, len(adj[v])):
                w = adj[v][j]
                if w not in index:
                    work.append((v, j + 1))
                    work.append((w, 0))
                    recurse = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.update(comp)
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return out


# ---------- the scene ----------

def scene(m: Model, st: ViewState) -> dict:
    expanded, hidden, visible, rolled, utilities = compute(m, st)
    root_group = lambda x: m.has(x) and m.entities[x].kind == "group" and m.parent(x) is None  # noqa: E731

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
    in_cycle = cycle_members([v for v in drawn if not is_body(v)], edges)

    services = [c for c in m.children("deploy") if m.entities[c].kind == "service"] if m.has("deploy") else []
    nodes: list[dict] = []

    def node(x: str, parent: str | None, container: bool) -> dict:
        if is_body(x):
            return {"id": x, "kind": "anchor", "parent": parent, "info": node_info(m, x, rolled, expanded, utilities, False, False)}
        e = m.entities[x]
        badge = badge_text(m, x, rolled, expanded)
        if container and st.hide_internal and internal_drawn.get(x):
            badge = " · ".join(filter(None, [badge, f"{internal_drawn[x]} internal"]))
        kids = bool(m.children(x))
        return {
            "id": x, "kind": e.kind, "parent": parent, "container": container, "label": label_of(m, x, expanded),
            "icon": icon_of(m, x), "foldable": kids, "leaves": leaf_count(m, x) if kids and not container else None,
            "badge": badge, "cycle": x in in_cycle, "utility": x in utilities,
            "chips": [{"id": s, "name": m.entities[s].name, "color": services.index(s) if s in services else -1}
                      for s in rolled.allocations.get(x, [])],
            "service_color": services.index(x) if x in services else None,
            "internal": internal_drawn.get(x, 0) if container else None,
            "info": node_info(m, x, rolled, expanded, utilities, x in in_cycle, container),
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
            "info": edge_info(m, e, expanded, drawn_parent(m, e.src, expanded) if local(e) else None, weak),
        })

    residue = [f"{len(hidden)} entities hidden by role", f"{rolled.hidden_relations} relations touch hidden entities"]
    residue += [f"{v} unresolved {k} references" for k, v in sorted(rolled.unresolved.items())]
    dangling = sum(1 for e in edges if e.subkind.endswith("-dangling"))
    if dangling:
        residue.append(f"{dangling} interface edges with one side only")
    if utilities:
        residue.append("utilities (badges): " + ", ".join(label_of(m, u, expanded) for u in sorted(utilities)))
    residue += [f"{v} {k} wiring edges not drawn (option)" for k, v in rolled.suppressed.items()]
    if in_cycle:
        residue.append(f"{len(in_cycle)} drawn nodes take part in cycles")

    groups = [{"id": r, "name": m.entities[r].name, "size": len(m.children(r)), "open": r in expanded}
              for r in m.roots() if m.entities[r].kind == "group" and r != "unresolved"]
    state = asdict(st) | {"expanded": sorted(expanded)}
    return {"state": state, "nodes": nodes, "edges": edge_out, "groups": groups, "residue": residue,
            "stats": {"nodes": len(drawn), "edges": len(edge_out)}}


def state_from_query(q: dict[str, str]) -> ViewState:
    """Parse the viewer's URL parameters (the same keys as its location hash)."""
    st = ViewState()
    if q.get("x"):
        st.expanded = [x for x in q["x"].split(",") if x]
    if q.get("r") is not None:
        st.roles = [r for r in q["r"].split(",") if r]
    if q.get("b"):
        st.budget = int(q["b"])
    st.through = q.get("t", "1") != "0"
    st.dangling_use = q.get("u") == "1"
    st.env_edges = q.get("ev") == "1"
    st.bundle = q.get("nb") != "1"
    st.utilities = q.get("nu") != "1"
    st.hide_internal = q.get("hi") == "1"
    return st
