"""Projection: a scene folded to what one picture draws.

A scene state (``ProjectionSpec``) is the set of unfolded containers (or a preset that names
one) plus the filters and options. ``project`` turns a scene into the scene of that
picture: the nodes filtered by the state, contracted onto the cut through the containment
tree that the unfolded containers make, and simplified by the roll-up rules (``rollup.py``,
``rollup.md``). Each drawn node keeps the address of what it is (a module's own code is
``<id>#``, ``address.own_code``) and refers to every node it stands for (``source``), each
drawn edge to the edges it bundles, so any overlay can say something about a drawn box from
what it says about what is inside.

What the projection itself decided is its own overlay, ``projection``: which boxes are
containers, which lines were lifted, utilities drawn as badges, deployment chips, how the
drawn edges count. What the picture shows besides is other overlays' (``label``, ``hover``,
``change``, ``review``). The scene's ``meta["projection"]`` keeps the state, the drawing
order, the filter's groups and roles, and what was left out (``residue``).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from ..model import CONTAIN, USE, EdgeKey, Key, Model, address, metamodel
from ..model.scene import Element, FixedOverlay, Scene
from .reading import Reading
from .rollup import (
    HIDDEN_ROLES_DEFAULT, RolledUp, chain_end, drawn_parent, hidden_by_role, image_aliases, is_init, lift,
    representative, rollup, utility_badges, visible_from_expanded,
)
from .unfold import find_default_cut

ALL_ROLES = ("source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other")
DEFAULT_ROLES = tuple(r for r in ALL_ROLES if r not in HIDDEN_ROLES_DEFAULT)
DEFAULT_BUDGET = 34
DEFAULT_UTILITY_IN = 4  # more incoming edges than this: a utility, drawn as badges
DEFAULT_MAX_LINES = 4  # lines between two drawn boxes at most; more are lifted to a box they are inside (rollup.lift)
DEFAULT_GROUPS = (address.root(address.API),)  # root groups drawn as boxes; the others are badges on their users
PRESETS = ("default", "folded", "changes", "unfolded")


@dataclass
class ProjectionSpec:
    """What a projection is asked for: which nodes become boxes (the unfolded containers,
    ``expanded``, or those ``unfold`` says yes to, or a preset: ``default`` the default cut for
    ``budget``, ``folded`` the top level, ``changes`` what ``focus`` names, ``unfolded``
    everything; then the roles and namespace groups shown), how edges are simplified
    (``utilities``, ``max_lines``), and for a change which side. A container unfolded inside a
    folded one stays folded."""
    expanded: list[str] | None = None
    preset: str = "default"
    roles: list[str] = field(default_factory=lambda: list(DEFAULT_ROLES))  # shown roles
    budget: int = DEFAULT_BUDGET
    groups: list[str] = field(default_factory=lambda: list(DEFAULT_GROUPS))  # shown root groups
    utilities: int = DEFAULT_UTILITY_IN  # boxes with more incoming edges are badges on their users; 0: off
    side: str = "both"  # a change: "base" or "head" draws one commit, "both" the merged picture
    max_lines: int = DEFAULT_MAX_LINES  # lines drawn between two boxes at most (rollup.lift)
    focus: list[str] | None = None  # what the "changes" preset unfolds; the caller says (for a change: nolli.review.changes.find_review_cut)
    unfold: Callable[[Element], bool] | None = field(default=None, repr=False, compare=False)  # instead of expanded: asked per container

    @classmethod
    def expanded_if(cls, unfold: Callable[[Element], bool], **rest) -> "ProjectionSpec":
        """Unfold every container ``unfold`` says yes to (asked about each, in the scene projected)."""
        return cls(unfold=unfold, **rest)


@dataclass
class Cut:
    """What a state cuts out of a scene: the unfolded containers reached, the hidden nodes, the
    drawn ones, the roll-up, the utilities."""
    expanded: set[str]
    hidden: set[str]
    visible: set[str]
    rolled: RolledUp
    utilities: set[str]


def hide_for(g: Reading, roles, side: str = "both") -> set[str]:
    """Files and directories whose role is not shown, plus images drawn as their service; in a
    change drawn for one side, what does not exist on that side."""
    shown = set(roles)
    out = hidden_by_role(g, {g.file(x)["role"] for x in g.nodes if g.file(x).get("role")} - shown)
    if side in ("base", "head"):
        gone = "added" if side == "base" else "removed"
        out |= {x for x in g.nodes if g.diff(x).get("status") == gone}
    return out | set(image_aliases(g))


def is_root_group(g: Reading, x: str) -> bool:
    return g.has(x) and g.kind(x) == "group" and g.parent(x) is None


def find_preset_cut(g: Reading, st: ProjectionSpec, hidden: set[str]) -> set[str]:
    """The containers a preset unfolds (a change's preset on a plain scene: the default cut)."""
    if st.preset == "folded":
        return {address.REPO_ROOT}
    if st.preset == "changes" and st.focus is not None:  # what the caller focuses on
        return {address.REPO_ROOT, *st.focus}
    if st.preset == "unfolded":
        return {x for x in g.nodes if x not in hidden and g.children(x)}
    hidden_roles = [r for r in ALL_ROLES if r not in set(st.roles)]
    return set(find_default_cut(g, budget=st.budget, hidden_roles=hidden_roles, hidden=hidden)["expanded"])


def cut(scene: Scene, st: ProjectionSpec) -> Cut:
    """``expanded`` is normalised to the containers actually reached, so folding a box also
    folds everything inside it."""
    g = Reading(scene)
    hidden = hide_for(g, st.roles, st.side)
    shown_groups = set(st.groups)
    if st.unfold is not None:
        expanded = {x for x in g.nodes if g.children(x) and st.unfold(scene.find_element(x))}
    else:
        expanded = find_preset_cut(g, st, hidden) if st.expanded is None else {x for x in st.expanded if g.has(x)}
    expanded = {x for x in expanded if not (is_root_group(g, x) and x not in shown_groups)}  # filtered out: stays folded
    walked: set[str] = set()
    visible = visible_from_expanded(g, expanded, hidden, walked=walked)
    expanded = walked
    rolled = rollup(g, visible, hidden, shown_groups=shown_groups, side=st.side)
    rolled.edges = lift(g, rolled.edges, expanded, st.max_lines)  # at most max_lines lines between two boxes
    utilities = utility_badges(g, rolled, st.utilities) if st.utilities > 0 else set()
    return Cut(expanded, hidden, visible, rolled, utilities)


def edge_style(e) -> str:
    if e.kind == "provide":
        return "provide"
    return e.subkind if e.subkind in ("import", "http", "env") else "deploy"


def leaf_count(g: Reading, x: str) -> int:
    """Files below a directory; leaves below anything else."""
    is_dir = g.kind(x) == "directory"
    n, stack = 0, list(g.children(x))
    while stack:
        c = stack.pop()
        k = g.kind(c)
        if is_dir:
            if k == "file":
                n += 0 if is_init(g, c) else 1
            elif k == "directory":
                stack.extend(g.children(c))
        elif not g.children(c):
            n += 1
        else:
            stack.extend(g.children(c))
    return n


def fold(st: "ProjectionSpec") -> Callable[[Scene], tuple[Model, FixedOverlay]]:
    """The projection as a transformation: what ``st`` asks for, of the scene it is given."""
    return lambda scene: fold_model(scene, st)


def fold_model(scene: Scene, st: "ProjectionSpec") -> tuple[Model, FixedOverlay]:
    """The folded model: its nodes the boxes drawn, each keeping its address and referring to
    what it stands for, its edges the lines drawn, each referring to the edges it bundles; and
    the projection's overlay, what it decided about them."""
    g = Reading(scene)
    c = cut(scene, st)
    expanded, hidden, visible, rolled, utilities = c.expanded, c.hidden, c.visible, c.rolled, c.utilities
    shown_groups = set(st.groups)
    # a root group is drawn when the filter shows it and it has something to unfold (rollup
    # leaves no edge on one the filter hides)
    root_group = lambda x: is_root_group(g, x) and (x not in shown_groups or not g.children(x))  # noqa: E731
    edges = rolled.edges
    out_total: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.kind == USE:
            out_total[e.src] += e.count
    touched = {e.src for e in edges} | {e.dst for e in edges}
    used_by: dict[str, int] = defaultdict(int)  # utility -> boxes whose edges into it became badges
    for ann in rolled.annotations.values():
        for grp in ann:
            if grp.startswith("util:"):
                used_by[grp[5:]] += 1
    services = [s for s in g.children("deploy") if g.kind(s) == "service"] if g.has("deploy") else []

    order: list[str] = []  # drawing order
    parent_of: dict[str, str | None] = {}
    values: dict[Key, dict] = {}

    def used_utils(x: str) -> list[str]:
        return sorted(grp[5:] for grp in (rolled.annotations.get(x) or {}) if grp.startswith("util:"))

    def node(x: str, parent: str | None, container: bool) -> None:
        order.append(x)
        parent_of[x] = parent
        if address.is_own_code(x):
            values[x] = {"container": False, "utilities_used": used_utils(x)}
            return
        kids = bool(g.children(x))
        values[x] = {
            "container": container, "foldable": kids, "leaves": leaf_count(g, x) if kids and not container else None,
            "badge": f"used by {used_by[x]}" if used_by.get(x) else "", "used_by": used_by.get(x, 0), "utility": x in utilities,
            "chips": [{"id": s, "name": g.segment(s), "color": services.index(s) if s in services else -1}
                      for s in rolled.allocations.get(x, [])],
            "service_color": services.index(x) if x in services else None,
            "utilities_used": used_utils(x),  # ids of the utilities whose edges from this box became badges
            "internal_changes": len(rolled.internal_changes.get(x) or {}),
        }

    def build(x: str, parent: str | None) -> bool:
        if x in expanded and g.children(x):
            me = None if x == address.REPO_ROOT else x  # the repository root is the canvas, not a box
            if me is not None:
                node(x, parent, True)
            ch: list[str] = []
            for kid in g.children(x):
                if kid in hidden:
                    continue
                if g.kind(x) == "directory" and is_init(g, kid):
                    ch.extend(g.children(kid))  # a package's __init__ classes are its children
                    continue
                ch.append(chain_end(g, kid, hidden) if g.kind(x) == "directory" and g.kind(kid) == "directory" else kid)
            n = 0
            if address.own_code(x) in visible:
                node(address.own_code(x), me or parent, False)
                n += 1
            for kid in ch:
                if kid in visible or (kid in expanded and g.children(kid)):
                    n += build(kid, me or parent)
            if me is not None and n == 0:
                values[x]["container"] = False
            return True
        if x not in visible or (root_group(x) and x not in touched):
            return False
        node(x, parent, False)
        return True

    for r in g.roots():
        if r not in hidden:
            build(r, None)

    drawn = set(order)
    # what each drawn box stands for, each node by exactly one box: the innermost drawn box
    # holding it (a container itself, what is inside belongs to what is drawn inside), a
    # package's own code what its package body holds
    rep = representative(g, visible, hidden, image_aliases(g))
    refs: dict[Key, list[Key]] = defaultdict(list)
    for x in sorted(g.nodes):
        if x in hidden:
            continue
        r = x if x in drawn and not address.is_own_code(x) else rep(x)
        if r is not None and r in drawn:
            refs[r].append(x)
    for x in order:
        if x in refs[x]:
            refs[x] = [x] + [k for k in refs[x] if k != x]

    def local(e) -> bool:
        ps = drawn_parent(g, e.src, expanded)
        return ps is not None and ps == drawn_parent(g, e.dst, expanded)

    keys: list[EdgeKey] = []
    for e in edges:
        if e.src not in drawn or e.dst not in drawn:
            continue
        k = EdgeKey(e.kind, e.src, e.dst, e.subkind)
        ot = out_total.get(e.src, 0)
        values[k] = {
            "count": e.count, "confidence": e.confidence, "via": sorted(e.via), "style": edge_style(e),
            "local": local(e), "single": e.count == 1, "weak": bool(ot) and e.kind == USE and e.count / ot < 0.05 and e.count < 3,
            "heuristic": e.confidence == "heuristic",
            "types": e.count > 0 and e.types == e.count,  # references only to types (``type_only``): no run-time dependency
            "type_refs": e.types, "names": dict(e.names),
            # a lifted line: the drawn nodes it stands for at each end (the viewer's ports lead there)
            "ends": ({"src": sorted(e.ends["src"]), "dst": sorted(e.ends["dst"])}
                     if e.ends["src"] - {e.src} or e.ends["dst"] - {e.dst} else None),
            "status": e.status if g.is_change else None, "base": e.base, "head": e.head, "changed": sorted(e.changed),
        }
        refs[k] = sorted(e.members)
        keys.append(k)

    kinds = {x: ("anchor" if address.is_own_code(x) else g.kind(x)) for x in order}
    contain = [EdgeKey(CONTAIN, p, x) for x, p in parent_of.items() if p is not None]
    residue = [f"{len(hidden)} entities hidden by role", f"{rolled.hidden_relations} relations touch hidden entities"]
    residue += [f"{v} unresolved {k} references" for k, v in sorted(rolled.unresolved.items())]
    groups = [{"id": r, "namespace": address.scheme_of(r), "name": metamodel.NAMESPACES[address.scheme_of(r)].title, "size": len(g.children(r)), "shown": r in shown_groups}
              for r in g.roots() if g.kind(r) == "group" and metamodel.NAMESPACES[address.scheme_of(r)].structural and g.children(r)]
    by_role: dict[str, dict[str, int]] = {r: {"files": 0, "dirs": 0} for r in ALL_ROLES}  # for the Show filter ("mixed" is a directory role only)
    for x, kind in g.nodes.items():
        r = g.file(x).get("role")
        if r in by_role and kind in ("file", "directory") and not g.file(x).get("root"):
            by_role[r]["files" if kind == "file" else "dirs"] += 1
    state = {k: v for k, v in asdict(st).items() if k not in ("focus", "unfold")} | {"expanded": sorted(expanded),
             "preset": None if st.expanded is not None else "default" if st.preset == "changes" and st.focus is None else st.preset}
    drawn_count = sum(1 for x in order if not (root_group(x) and x not in touched))
    meta = {"projection": {"state": state, "order": order, "groups": groups, "roles": [{"id": r, **n} for r, n in by_role.items()],
                           "residue": residue, "utilities": sorted(utilities), "expanded": sorted(expanded),
                           "hidden": len(hidden), "hidden_nodes": sorted(hidden), "stats": {"nodes": drawn_count, "edges": len(keys)},
                           "internal": dict(rolled.internal), "hidden_relations": rolled.hidden_relations,
                           "unresolved": dict(rolled.unresolved)}}
    key = "project:" + hashlib.sha1(f"{scene.model.key}|{sorted(state.items())}".encode()).hexdigest()[:16]
    model = Model(key, kinds, [*contain, *keys], refs={"source": {k: tuple(v) for k, v in refs.items()}}, meta=meta, check=False)
    return model, FixedOverlay("projection", values)
