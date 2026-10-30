"""The scene: everything the viewer draws for one view state, computed in one place.

A view state is the set of unfolded containers plus the display options. ``scene``
turns it into a flat list of nodes (in drawing order, each naming its drawn parent) and
a list of edges, with labels, badges, emphasis flags and tooltips already decided. The
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

ALL_ROLES = ("source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other")
DEFAULT_ROLES = tuple(r for r in ALL_ROLES if r not in HIDDEN_ROLES_DEFAULT)
DEFAULT_BUDGET = 34
DEFAULT_UTILITY_IN = 6  # more incoming edges than this: a utility, drawn as badges
DEFAULT_GROUPS = ("http",)  # root groups drawn as boxes; the others are badges on their users
GROUP_SHORT = {"ext": "pkgs", "env": "env", "deploy": "deploy", "http": "api"}
MODULE_LANGS = ("python", "typescript", "javascript")


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


def hidden_for(m: Model, roles) -> set[str]:
    """Files and directories whose role is not shown, plus images drawn as their service."""
    shown = set(roles)
    out = {e.id for e in m.entities.values()
           if e.kind in ("file", "directory") and not e.attrs.get("root") and e.attrs.get("role") and e.attrs["role"] not in shown}
    return out | set(image_aliases(m))


def is_root_group(m: Model, x: str) -> bool:
    return m.has(x) and m.entities[x].kind == "group" and m.parent(x) is None


def compute(m: Model, st: ViewState) -> tuple[set[str], set[str], set[str], RolledUp, set[str]]:
    """(expanded, hidden, visible, rolled, utilities) for a state; expanded is normalised to
    the containers actually reached, so folding a box also folds everything inside it."""
    hidden = hidden_for(m, st.roles)
    shown_groups = set(st.groups)
    if st.expanded is None:
        hidden_roles = [r for r in ALL_ROLES if r not in set(st.roles)]
        expanded = set(default_view(m, budget=st.budget, hidden_roles=hidden_roles)["expanded"])
    else:
        expanded = {x for x in st.expanded if m.has(x)}
    expanded = {x for x in expanded if not (is_root_group(m, x) and x not in shown_groups)}  # filtered out: stays folded
    walked: set[str] = set()
    visible = visible_from_expanded(m, expanded, hidden, walked=walked)
    expanded = walked
    rolled = rollup(m, visible, hidden, through_interfaces=st.through,
                    dangling_use_kinds=("endpoint", "env") if st.dangling_use else (), env_edges=st.env_edges,
                    shown_groups=shown_groups)
    if st.bundle:
        rolled.edges = bundle(m, rolled.edges, expanded)
    utilities = utility_badges(m, rolled, st.utilities) if st.utilities > 0 else set()
    return expanded, hidden, visible, rolled, utilities


# ---------- labels ----------

def is_body(x: str) -> bool:
    return x.startswith("body:")


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


def label_of(m: Model, x: str, expanded: set[str]) -> str:
    if is_body(x):
        return ""
    e = m.entities[x]
    if e.kind == "directory":
        return e.name if e.attrs.get("root") else display_path(m, x, expanded)
    if e.kind == "file" and e.attrs.get("language") in MODULE_LANGS:
        return e.name.rsplit(".", 1)[0] if "." in e.name else e.name  # a source file is its module
    if e.kind == "service":
        return "svc " + e.name
    if e.kind == "image":
        return "img " + e.name.removesuffix("/Dockerfile")
    return e.name


def leaf_count(m: Model, x: str) -> int:
    """Files below a directory; leaves below anything else."""
    is_dir = m.entities[x].kind == "directory"
    n, stack = 0, list(m.children(x))
    while stack:
        c = stack.pop()
        k = m.entities[c].kind
        if is_dir:
            if k == "file":
                n += 0 if is_init(m, c) else 1
            elif k == "directory":
                stack.extend(m.children(c))
        elif not m.children(c):
            n += 1
        else:
            stack.extend(m.children(c))
    return n


def badge_text(m: Model, x: str, rolled: RolledUp, used_by: int = 0) -> str:
    """Counts of edges to filtered-out groups ("19 pkgs"), and for a utility how many boxes
    use it. Which utilities a box uses is not here: that is the container's ``uses`` list."""
    ann = rolled.annotations.get(x) or {}
    parts = [f"used by {used_by}"] if used_by else []
    parts += [f"{c} {GROUP_SHORT.get(g, g)}" for g, c in sorted(ann.items()) if not g.startswith("util:")]
    return " · ".join(parts)


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


def node_tip(m: Model, x: str, rolled: RolledUp, utilities: set[str]) -> str:
    if is_body(x):
        o = m.entities[x[5:]]
        if o.kind == "directory":
            return f"the package's own code: {o.attrs.get('path')}/__init__.py\n(what it does beyond re-exporting its own modules)"
        return f"the module's own code: {o.attrs.get('path')}\n(functions, constants and statements outside any class)"
    e = m.entities[x]
    kids = m.children(x)
    lines = [f"{e.kind}  {x}"]
    if e.kind == "file" and kids:
        lines.append(f"{len(kids)} classes (click to unfold)")
    for key, fmt in (("line", "line {}"), ("path", "{}")):
        if e.attrs.get(key):
            lines.append(fmt.format(e.attrs[key]))
    if e.attrs.get("language"):
        langs = e.attrs.get("languages")
        extra = "  " + ", ".join(f"{k} {v / 1000:.0f}k" for k, v in langs.items()) if langs else ""
        lines.append("language: " + e.attrs["language"] + extra)
    if e.attrs.get("role"):
        lines.append("role: " + e.attrs["role"])
    if kids:
        lines.append(f"{len(kids)} children, {leaf_count(m, x)} leaves, {rolled.internal.get(x, 0)} internal relations")
    for g, c in (rolled.annotations.get(x) or {}).items():
        if g.startswith("util:"):
            lines.append(f"{c} references to {label_of(m, g[5:], set())} (utility, drawn as badge)")
        else:
            lines.append(f"{c} relations with {m.entities[g].name} (folded)")
    if x in utilities:
        lines.append("utility: more incoming edges than the threshold; they are badges on the boxes that use it")
    if rolled.allocations.get(x):
        lines.append("deployed in: " + ", ".join(m.entities[s].name for s in rolled.allocations[x]))
    if e.attrs.get("image"):
        lines.append("image: " + str(e.attrs["image"]))
    if e.attrs.get("ports"):
        lines.append("ports: " + ", ".join(map(str, e.attrs["ports"])))
    return "\n".join(lines)


def edge_tip(e) -> str:
    lines = [f"{e.kind} {e.subkind}  ×{e.count}  ({e.confidence})", f"{e.src} → {e.dst}"]
    via = sorted(e.via)
    if via:
        lines.append("via: " + "\n     ".join(via[:14]) + (f"\n     … {len(via) - 14} more" if len(via) > 14 else ""))
    return "\n".join(lines)


# ---------- the scene ----------

def scene(m: Model, st: ViewState) -> dict:
    expanded, hidden, visible, rolled, utilities = compute(m, st)
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

    def used_utils(x: str) -> list[str]:
        return sorted(g[5:] for g in (rolled.annotations.get(x) or {}) if g.startswith("util:"))

    def node(x: str, parent: str | None, container: bool) -> dict:
        if is_body(x):
            return {"id": x, "kind": "anchor", "parent": parent, "utilities_used": used_utils(x), "tip": node_tip(m, x, rolled, utilities)}
        e = m.entities[x]
        badge = badge_text(m, x, rolled, used_by.get(x, 0))
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
            "uses": [],  # unfolded boxes: the utilities their children use (filled in below)
            "utilities_used": used_utils(x),  # ids of the utilities whose edges from this box became badges
            "tip": node_tip(m, x, rolled, utilities),
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

    # the utilities a box's children use are listed in that box once it is unfolded (one
    # line each, bottom right); a folded box lists none, and nothing is summed upwards
    by_id = {n["id"]: n for n in nodes}
    uses: dict[str, set[str]] = defaultdict(set)
    for src, ann in rolled.annotations.items():
        rec = by_id.get(src)
        if rec is None:
            continue
        holder = src if rec.get("container") else rec["parent"]
        for g in ann:
            if g.startswith("util:") and holder is not None:
                uses[holder].add(g[5:])
    for rec in nodes:
        if rec.get("container"):
            rec["uses"] = sorted((label_of(m, u, expanded) for u in uses.get(rec["id"], ())), key=str.lower)

    edge_out = []
    for e in edges:
        ot = out_total.get(e.src, 0)
        edge_out.append({
            "key": f"{e.kind}|{e.src}|{e.dst}|{e.subkind}", "kind": e.kind, "subkind": e.subkind, "src": e.src, "dst": e.dst,
            "count": e.count, "confidence": e.confidence, "via": sorted(e.via), "style": edge_style(e), "label": edge_label(e),
            "local": local(e), "single": e.count == 1,
            "weak": bool(ot) and e.kind == USE and e.count / ot < 0.05 and e.count < 3,
            "dangling": e.subkind.endswith("-dangling"), "heuristic": e.confidence == "heuristic", "tip": edge_tip(e),
        })

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
    if q.get("g") is not None:
        st.groups = [g for g in q["g"].split(",") if g]
    st.through = q.get("t", "1") != "0"
    st.dangling_use = q.get("u") == "1"
    st.env_edges = q.get("ev") == "1"
    st.bundle = q.get("nb") != "1"
    st.utilities = 0 if q.get("nu") == "1" else int(q["ut"]) if q.get("ut") else DEFAULT_UTILITY_IN
    st.hide_internal = q.get("hi") == "1"
    return st
