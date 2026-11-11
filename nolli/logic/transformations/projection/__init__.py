"""Applies a projection, which folds (collapses) some entities into aggregates according to a ``ProjectionSpec``.

The package holds what only a projection needs: what it reads off a scene (``reading``), the
roll-up of the dependency graph to the visible nodes (``rollup``) and the choice of which
containers to unfold for a node budget (``unfold``)."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from nolli.logic.transformations.projection import rollup
from nolli.logic.transformations.projection import unfold as unfold_lib
from nolli.logic.transformations.projection.reading import Reading
from nolli.logic.transformations.projection.rollup import RolledUp
from nolli.model import vocabulary
from nolli.model.keys import StringKey
from nolli.model.model import Entity, Key, Model, Relation
from nolli.model.scene import Element, FixedOverlay, Scene
from nolli.model.schema import EntityId, EntityKind, RelationKind, Scheme

ALL_ROLES = ("source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other")
DEFAULT_ROLES = tuple(r for r in ALL_ROLES if r not in rollup.HIDDEN_ROLES_DEFAULT)
DEFAULT_BUDGET = 34
DEFAULT_UTILITY_IN = 4  # more incoming edges than this: a utility, drawn as badges
DEFAULT_MAX_LINES = 4  # lines between two drawn boxes at most; more are lifted to a box they are inside (rollup.lift)
DEFAULT_GROUPS = (EntityId.root(Scheme.API),)  # root groups drawn as boxes; the others are badges on their users


class Expansion:
    """Which containers a projection unfolds. ``find`` answers for the scene read by ``g``, given
    what is hidden and the rest of the spec. ``name``: what the viewer calls it (its presets),
    None for an explicit list or a predicate."""

    name: str | None = None

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        raise NotImplementedError

    def name_for(self, g: Reading) -> str | None:
        """Return what the viewer calls what ``find`` did on ``g``; a rule may fall back to another."""
        return self.name


@dataclass(frozen=True)
class Ids(Expansion):
    """These containers (those the scene has)."""
    ids: tuple[str, ...]

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {x for x in self.ids if g.has(x)}


@dataclass(frozen=True)
class Where(Expansion):
    """Every container ``unfold`` says yes to, asked about each in the scene projected."""
    unfold: Callable[[Element], bool] = field(repr=False, compare=False)

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {x for x in g.nodes if g.children(x) and self.unfold(g.scene.find_element(x))}


@dataclass(frozen=True)
class Root(Expansion):
    """The top level only."""
    name = "folded"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {EntityId.REPO_ROOT}


@dataclass(frozen=True)
class All(Expansion):
    """Every container that is not hidden."""
    name = "unfolded"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {x for x in g.nodes if x not in hidden and g.children(x)}


@dataclass(frozen=True)
class AtMost(Expansion):
    """The default cut: as much unfolded as about ``budget`` boxes allow (hidden roles do not count)."""
    budget: int = DEFAULT_BUDGET
    name = "default"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        hidden_roles = [r for r in ALL_ROLES if r not in set(st.roles)]
        return set(unfold_lib.find_default_cut(g, budget=self.budget, hidden_roles=hidden_roles, hidden=hidden)["expanded"])


PRESETS: dict[str, type[Expansion]] = {"default": AtMost, "folded": Root, "unfolded": All}  # the viewer's presets that need nothing but the scene


@dataclass
class ProjectionSpec:
    """What a projection is asked for: which containers are unfolded (``expand``), the roles and
    namespace groups shown, how edges are simplified (``utilities``, ``max_lines``), and for a
    change which side. A container unfolded inside a folded one stays folded."""
    expand: Expansion = field(default_factory=AtMost)
    roles: list[str] = field(default_factory=lambda: list(DEFAULT_ROLES))  # shown roles
    groups: list[str] = field(default_factory=lambda: list(DEFAULT_GROUPS))  # shown root groups
    utilities: int = DEFAULT_UTILITY_IN  # boxes with more incoming edges are badges on their users; 0: off
    side: str = "both"  # for a change, "base" or "head" draws one commit and "both" the merged model
    max_lines: int = DEFAULT_MAX_LINES  # lines drawn between two boxes at most (rollup.lift)


@dataclass
class Cut:
    """What a state cuts out of a scene: the unfolded containers reached, the hidden nodes, the
    drawn ones, the roll-up, the utilities."""
    expanded: set[str]
    hidden: set[str]
    visible: set[str]
    rolled: RolledUp
    utilities: set[str]


def _hide_for(g: Reading, roles, side: str = "both") -> set[str]:
    """Files and directories whose role is not shown, plus images drawn as their service; in a
    change drawn for one side, what does not exist on that side."""
    shown = set(roles)
    out = rollup.hidden_by_role(g, {g.file(x)["role"] for x in g.nodes if g.file(x).get("role")} - shown)
    if side in ("base", "head"):
        gone = "added" if side == "base" else "removed"
        out |= {x for x in g.nodes if g.diff(x).get("status") == gone}
    return out | set(rollup.image_aliases(g))


def _is_root_group(g: Reading, x: str) -> bool:
    return g.has(x) and g.kind(x) == "group" and g.parent(x) is None


def _cut(g: Reading, st: ProjectionSpec) -> Cut:
    """Work out what ``st`` cuts out of the scene ``g`` reads. ``expanded`` is normalised to the
    containers actually reached, so folding a box also folds everything inside it."""
    hidden = _hide_for(g, st.roles, st.side)
    shown_groups = set(st.groups)
    expanded = st.expand.find(g, hidden, st)
    expanded = {x for x in expanded if not (_is_root_group(g, x) and x not in shown_groups)}  # filtered out: stays folded
    walked: set[str] = set()
    visible = rollup.visible_from_expanded(g, expanded, hidden, walked=walked)
    expanded = walked
    rolled = rollup.rollup(g, visible, hidden, shown_groups=shown_groups, side=st.side)
    rolled.edges = rollup.lift(g, rolled.edges, expanded, st.max_lines)  # at most max_lines lines between two boxes
    utilities = rollup.utility_badges(g, rolled, st.utilities) if st.utilities > 0 else set()
    return Cut(expanded, hidden, visible, rolled, utilities)


def _edge_style(e) -> str:
    if e.kind == "provide":
        return "provide"
    return e.subkind if e.subkind in ("import", "http", "env") else "deploy"


def _leaf_count(g: Reading, x: str) -> int:
    """Files below a directory; leaves below anything else."""
    is_dir = g.kind(x) == "directory"
    n, stack = 0, list(g.children(x))
    while stack:
        c = stack.pop()
        k = g.kind(c)
        if is_dir:
            if k == "file":
                n += 0 if rollup.is_init(g, c) else 1
            elif k == "directory":
                stack.extend(g.children(c))
        elif not g.children(c):
            n += 1
        else:
            stack.extend(g.children(c))
    return n


def fold(st: ProjectionSpec) -> Callable[[Scene], Scene]:
    """Return the projection as a transformation, which projects the scene it is given as ``st`` asks.

    The nodes are filtered by ``st``, contracted onto the cut through the containment tree
    that the unfolded containers make, and simplified by the roll-up rules (``rollup``).
    Each drawn node keeps the address of what it is (a module's own code is ``<id>#``,
    ``EntityId.own_code``) and refers to every node it stands for (role ``source``), each
    drawn edge to the edges it bundles, so any overlay can say something about a drawn box
    from what it says about what is inside. What the filter hides (roles not in
    ``st.roles``, what the other side lacks) no drawn node stands for: it is cut, so a change
    only to hidden files leaves its box unchanged.

    What the projection itself decided is its own overlay, ``projection``: which boxes are
    containers, which lines were lifted, utilities drawn as badges, deployment chips, how the
    drawn edges count. The model's ``meta["projection"]`` keeps the state, the drawing
    order, the filter's groups and roles, and what was left out (``residue``)."""
    def make(scene: Scene) -> Scene:
        model, refs, decided = _fold_model(scene, st)
        return Scene(model, [decided], {"source": scene}, {"source": refs})

    return make


def _fold_model(scene: Scene, st: ProjectionSpec) -> tuple[Model, dict[Key, tuple[Key, ...]], FixedOverlay]:
    """Build the folded model and the projection's overlay. The model's nodes are the boxes drawn,
    each keeping its address and referring to what it stands for; its edges are the lines drawn,
    each referring to the edges it bundles. The overlay holds what the projection decided about
    them."""
    g = Reading(scene)
    c = _cut(g, st)
    expanded, hidden, visible, rolled, utilities = c.expanded, c.hidden, c.visible, c.rolled, c.utilities
    shown_groups = set(st.groups)
    # a root group is drawn when the filter shows it and it has something to unfold (rollup
    # leaves no edge on one the filter hides)
    root_group = lambda x: _is_root_group(g, x) and (x not in shown_groups or not g.children(x))
    edges = rolled.edges
    out_total: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.kind == RelationKind.USE:
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
        if x.is_own_code:
            values[x] = {"container": False, "utilities_used": used_utils(x)}
            return
        kids = bool(g.children(x))
        values[x] = {
            "container": container, "foldable": kids, "leaves": _leaf_count(g, x) if kids and not container else None,
            "badge": f"used by {used_by[x]}" if used_by.get(x) else "", "used_by": used_by.get(x, 0), "utility": x in utilities,
            "chips": [{"id": s, "name": g.segment(s), "color": services.index(s) if s in services else -1}
                      for s in rolled.allocations.get(x, [])],
            "service_color": services.index(x) if x in services else None,
            "utilities_used": used_utils(x),  # ids of the utilities whose edges from this box became badges
            "internal_changes": len(rolled.internal_changes.get(x) or {}),
        }

    def build(x: str, parent: str | None) -> bool:
        if x in expanded and g.children(x):
            me = None if x == EntityId.REPO_ROOT else x  # the repository root is the canvas, not a box
            if me is not None:
                node(x, parent, True)
            ch: list[str] = []
            for kid in g.children(x):
                if kid in hidden:
                    continue
                if g.kind(x) == "directory" and rollup.is_init(g, kid):
                    ch.extend(g.children(kid))  # a package's __init__ classes are its children
                    continue
                ch.append(rollup.chain_end(g, kid, hidden) if g.kind(x) == "directory" and g.kind(kid) == "directory" else kid)
            n = 0
            if x.own_code() in visible:
                node(x.own_code(), me or parent, False)
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
    rep = rollup.representative(g, visible, hidden, rollup.image_aliases(g))
    refs: dict[Key, list[Key]] = defaultdict(list)
    for x in sorted(g.nodes):
        if x in hidden:
            continue
        r = x if x in drawn and not x.is_own_code else rep(x)
        if r is not None and r in drawn:
            refs[r].append(x)
    for x in order:
        if x in refs[x]:
            refs[x] = [x] + [k for k in refs[x] if k != x]

    def local(e) -> bool:
        ps = rollup.drawn_parent(g, e.src, expanded)
        return ps is not None and ps == rollup.drawn_parent(g, e.dst, expanded)

    keys: list[Relation] = []
    for e in edges:
        if e.src not in drawn or e.dst not in drawn:
            continue
        k = Relation(e.kind, e.src, e.dst, e.subkind)
        ot = out_total.get(e.src, 0)
        values[k] = {
            "count": e.count, "confidence": e.confidence, "via": sorted(e.via), "style": _edge_style(e),
            "local": local(e), "single": e.count == 1, "weak": bool(ot) and e.kind == RelationKind.USE and e.count / ot < 0.05 and e.count < 3,
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

    entities = [Entity(x, EntityKind.ANCHOR if x.is_own_code else g.kind(x)) for x in order]
    contain = [Relation(RelationKind.CONTAIN, p, x) for x, p in parent_of.items() if p is not None]
    residue = [f"{len(hidden)} entities hidden by role", f"{rolled.hidden_relations} relations touch hidden entities"]
    residue += [f"{v} unresolved {k} references" for k, v in sorted(rolled.unresolved.items())]
    groups = [{"id": r, "namespace": r.scheme, "name": vocabulary.read_external(g.scene.find_element(r)).get("name") or r, "size": len(g.children(r)), "shown": r in shown_groups}
              for r in g.roots() if g.kind(r) == "group" and g.children(r)]
    by_role: dict[str, dict[str, int]] = {r: {"files": 0, "dirs": 0} for r in ALL_ROLES}  # for the Show filter ("mixed" is a directory role only)
    for x, kind in ((n.id, n.kind) for n in g.nodes.values()):
        r = g.file(x).get("role")
        if r in by_role and kind in ("file", "directory") and not g.file(x).get("root"):
            by_role[r]["files" if kind == "file" else "dirs"] += 1
    state = {k: v for k, v in asdict(st).items() if k != "expand"} | {"expanded": sorted(expanded), "preset": st.expand.name_for(g),
                                                                     "budget": getattr(st.expand, "budget", None)}
    drawn_count = sum(1 for x in order if not (root_group(x) and x not in touched))
    meta = {"projection": {"state": state, "order": order, "groups": groups, "roles": [{"id": r, **n} for r, n in by_role.items()],
                           "residue": residue, "utilities": sorted(utilities), "expanded": sorted(expanded),
                           "hidden": len(hidden), "hidden_nodes": sorted(hidden), "stats": {"nodes": drawn_count, "edges": len(keys)},
                           "internal": dict(rolled.internal), "hidden_relations": rolled.hidden_relations,
                           "unresolved": dict(rolled.unresolved)}}
    key = StringKey("project:" + hashlib.sha1(f"{scene.model.key.format_key()}|{sorted(state.items())}".encode()).hexdigest()[:16])
    model = Model(key, entities, [*contain, *keys], meta=meta, check=False)
    return model, {k: tuple(v) for k, v in refs.items()}, FixedOverlay("projection", values)
