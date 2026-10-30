"""Roll-up: derive the dependency graph at a chosen level of detail.

A *cut* is the set of visible entities. Every entity is represented by itself
if visible, else by its nearest visible ancestor, else it is hidden. A
relation between two entities becomes a relation between their
representatives; relations that land on one node are *internal* to it and
counted separately (they measure the structure a folded node hides).

Two conventions keep the picture readable:

- Loose files of an unfolded directory are drawn as one pseudo node
  ``files:<dir>`` inside it rather than one box per file (a directory's own
  files are its "body"; its subdirectories are its parts). Groups (externals,
  interfaces, deployment) unfold to individual children.
- Groups (External packages, Environment, Deployment, HTTP API) are a filter:
  a group the viewer shows is a box like any other, folded or unfolded; edges to
  a group it doesn't show are not drawn and become annotations on the other node
  ("uses 42 external packages"). Cross-cutting things are omnipresent; drawn,
  they hide the structure.

Interfaces (endpoints, env variables) whose group is not shown are derived
through: ``use(a, I)`` plus ``provide(b, I)`` yields
``use(rep a, rep b)`` with the interface as ``via``. An interface with only
one side keeps an edge to the interface's visible group for the kinds where
the dangling half is informative (an endpoint nobody in the repo calls is
still the service's surface; an env variable set but never read is noise).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .model import ALLOCATE, PROVIDE, USE, Model

CONF_RANK = {"exact": 0, "heuristic": 1, "statistical": 2}
HIDDEN_ROLES_DEFAULT = ("test", "docs", "config", "markup", "data", "asset", "other")
DERIVE_KINDS = ("endpoint", "env")
DANGLING_PROVIDE_KINDS = ("endpoint",)
DANGLING_USE_KINDS = ()


@dataclass
class RolledEdge:
    kind: str
    src: str
    dst: str
    subkind: str
    count: int = 0
    confidence: str = "exact"
    via: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "src": self.src, "dst": self.dst, "subkind": self.subkind, "count": self.count,
                "confidence": self.confidence, "via": sorted(self.via)}


@dataclass
class RolledUp:
    visible: list[str]
    edges: list[RolledEdge]
    internal: dict[str, int]
    annotations: dict[str, dict[str, int]]  # node -> folded group -> count
    allocations: dict[str, list[str]]  # code node -> drawn services it is copied into (chips, not edges)
    hidden_relations: int
    unresolved: dict[str, int]
    suppressed: dict[str, int] = field(default_factory=dict)  # edge subkind -> count, hidden by option

    def to_dict(self) -> dict:
        return {"visible": self.visible, "edges": [e.to_dict() for e in self.edges], "internal": self.internal,
                "annotations": self.annotations, "allocations": self.allocations,
                "hidden_relations": self.hidden_relations, "unresolved": self.unresolved, "suppressed": self.suppressed}


def image_aliases(m: Model) -> dict[str, str]:
    """image -> service, for images that run as exactly one service (drawn as that service)."""
    runs: dict[str, list[str]] = defaultdict(list)
    for r in m.relations:
        if r.kind == ALLOCATE and r.subkind == "runs_as":
            runs[r.src].append(r.dst)
    return {img: svcs[0] for img, svcs in runs.items() if len(svcs) == 1}


def hidden_by_role(m: Model, roles=HIDDEN_ROLES_DEFAULT) -> set[str]:
    rs = set(roles)
    return {e.id for e in m.entities.values() if e.kind in ("file", "directory") and e.attrs.get("role") in rs and not e.attrs.get("root")}


def chain_end(m: Model, n: str, hidden: set[str]) -> str:
    """Follow single-child directory chains (``frontend/playground/src``) to their end.

    A directory whose only non-hidden content is one subdirectory adds a level without
    adding structure; the chain is drawn as one node labelled with the joined path.
    """
    while m.entities[n].kind == "directory":
        kids = [c for c in m.children(n) if c not in hidden and not is_init(m, c)]
        if len(kids) == 1 and m.entities[kids[0]].kind == "directory":
            n = kids[0]
        else:
            break
    return n


def is_file(m: Model, x: str) -> bool:
    return m.entities[x].kind == "file"


def is_init(m: Model, x: str) -> bool:
    """A Python package's ``__init__.py``: the body of the package, not a module of its own."""
    e = m.entities.get(x)
    return e is not None and e.kind == "file" and e.name == "__init__.py"


def under(m: Model, x: str, d: str) -> bool:
    """x (or the file owning x) lies in directory d's subtree."""
    cur: str | None = x
    while cur is not None:
        if cur == d:
            return True
        cur = m.parent(cur)
    return False


def default_init_edge(m: Model, r) -> bool:
    """An edge from a package body to something inside its own package: the re-export /
    submodule import every ``__init__`` has. Not a dependency; dropped from the picture."""
    if not is_init(m, r.src):
        return False
    d = m.parent(r.src)
    return d is not None and under(m, r.dst, d)


def init_anchor_dirs(m: Model, hidden: set[str]) -> set[str]:
    """Directories whose ``__init__`` body still has edges after the default ones are
    dropped: these get an anchor node when unfolded, the others get nothing."""
    out: set[str] = set()
    drawn_kinds = ("directory", "file", "class", "service", "image")  # badge targets (externals, env, endpoints) don't count
    for r in m.relations:
        if r.kind not in (USE, PROVIDE, ALLOCATE) or default_init_edge(m, r):
            continue
        for end, other in ((r.src, r.dst), (r.dst, r.src)):
            if is_init(m, end) and end not in hidden and m.has(other) and m.entities[other].kind in drawn_kinds:
                d = m.parent(end)
                if d is not None:
                    out.add(d)
    return out


def file_degree(m: Model, hidden: set[str]) -> dict[str, int]:
    """Relations touching each leaf entity, for ranking which files to show first."""
    deg: dict[str, int] = defaultdict(int)
    for r in m.relations:
        if r.kind in (USE, PROVIDE, ALLOCATE) and r.src not in hidden and r.dst not in hidden:
            deg[r.src] += r.count
            deg[r.dst] += r.count
    return dict(deg)


def shown_files(m: Model, d: str, hidden: set[str], k: int, degree: dict[str, int]) -> tuple[list[str], list[str]]:
    """The k most connected files of d (ties by name), and the rest."""
    loose = sorted((c for c in m.children(d) if c not in hidden and is_file(m, c)),
                   key=lambda c: (-degree.get(c, 0), c))
    return loose[:k], loose[k:]


def visible_from_expanded(m: Model, expanded: set[str], hidden: set[str], show_files=None, degree=None,
                          walked: set[str] | None = None) -> set[str]:
    """The cut for a set of unfolded containers.

    An unfolded container shows *all* its non-hidden children (a partially opened
    container hides problems); children that are unfolded themselves are walked. An
    unfolded module also gets ``body:<module>``, the anchor for its own code. Single-child
    directory chains are compressed to their end, so ``expanded`` should name chain ends.
    A package's ``__init__`` is not a node: its classes are children of the package and its
    body is the package's anchor ``body:<dir>``, present only if it has edges.
    ``show_files`` and ``degree`` are accepted for compatibility and ignored. ``walked``,
    if given, receives the unfolded containers actually reached (the effective unfold set).
    """
    visible: set[str] = set()
    anchors = init_anchor_dirs(m, hidden)

    def walk(n: str) -> None:
        if walked is not None:
            walked.add(n)
        kind = m.entities[n].kind
        if kind == "file":
            visible.add(f"body:{n}")
        if kind == "directory" and n in anchors:
            visible.add(f"body:{n}")
        for c in m.children(n):
            if c in hidden:
                continue
            if kind == "directory" and is_init(m, c):
                visible.update(cc for cc in m.children(c) if cc not in hidden)  # the package's own classes
                continue
            if kind == "directory" and m.entities[c].kind == "directory":
                c = chain_end(m, c, hidden)
            if c in expanded and m.children(c):
                walk(c)
            else:
                visible.add(c)

    for r in m.roots():
        if r in hidden:
            continue
        if r in expanded and m.children(r):
            walk(r)
        else:
            visible.add(r)
    return visible


def display_path(m: Model, eid: str, expanded: set[str]) -> str:
    """A directory's path relative to its nearest unfolded ancestor (chains show their full run)."""
    parts = [m.entities[eid].name]
    p = m.parent(eid)
    while p is not None and p not in expanded and not m.entities[p].attrs.get("root"):
        parts.append(m.entities[p].name)
        p = m.parent(p)
    return "/".join(reversed(parts))


def representative(m: Model, visible: set[str], hidden: set[str], alias: dict[str, str] | None = None):
    cache: dict[str, str | None] = {}
    alias = alias or {}

    def rep(x: str) -> str | None:
        if x in cache:
            return cache[x]
        cur: str | None = alias.get(x, x)
        result: str | None = None
        while cur is not None:
            if cur in hidden:
                result = None
                break
            if cur in visible:
                result = cur
                break
            if m.entities[cur].kind == "file" and f"body:{cur}" in visible:
                result = f"body:{cur}"  # the module is unfolded: its own code
                break
            if is_init(m, cur):
                p = m.parent(cur)
                if p is not None and f"body:{p}" in visible:
                    result = f"body:{p}"  # the package is unfolded: its own code
                    break
            cur = m.parent(cur)
        cache[x] = result
        return result

    return rep


def owner_of(node: str) -> str:
    return node.split(":", 1)[1] if node.startswith(("files:", "body:")) else node


def top_of(m: Model, node: str, expanded: set[str]) -> str:
    """The outermost drawn ancestor of a drawn node (the top-level component it belongs to)."""
    cur = node
    while True:
        p = drawn_parent(m, cur, expanded)
        if p is None:
            return cur
        cur = p


def utility_badges(m: Model, rolled: "RolledUp", min_in: int = 6) -> set[str]:
    """Boxes with more than ``min_in`` incoming dependency edges in the picture are
    utilities: all those edges become badges on their sources ("→ observability"), like
    externals, since a box that many arrows point at mostly adds clutter. Edges are counted
    as drawn (one per arrow, whatever its count); groups and what's inside them are never
    utilities, so a shown group keeps its edges. Returns the utility nodes."""
    def in_group(x: str) -> bool:
        cur: str | None = owner_of(x)
        while cur is not None and m.has(cur):
            if m.entities[cur].kind == "group":
                return True
            cur = m.parent(cur)
        return False

    incoming: dict[str, int] = defaultdict(int)
    for e in rolled.edges:
        if e.kind == USE:
            incoming[e.dst] += 1
    utilities = {d for d, n in incoming.items() if n > min_in and not in_group(d)}
    if not utilities:
        return utilities
    kept = []
    for e in rolled.edges:
        if e.kind == USE and e.dst in utilities:
            rolled.annotations.setdefault(e.src, {})
            rolled.annotations[e.src]["util:" + e.dst] = rolled.annotations[e.src].get("util:" + e.dst, 0) + e.count
        else:
            kept.append(e)
    rolled.edges = kept
    return utilities


def reveal_scores(m: Model, hidden: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    """What unfolding reveals, per container.

    ``lca[C]`` counts relations whose endpoints lie in different children of C (their
    lowest common ancestor is C): exactly the edges that appear when C is unfolded.
    ``loose[C]`` counts relations between two loose files of C: the edges that appear
    when C's files node is opened. Relations touching hidden entities, or crossing
    between roots (code to an interface), do not count.
    """
    alias = image_aliases(m)
    lca: dict[str, int] = defaultdict(int)
    loose: dict[str, int] = defaultdict(int)
    anc_cache: dict[str, list[str] | None] = {}

    def chain(x: str) -> list[str] | None:
        if x in anc_cache:
            return anc_cache[x]
        out = [x]
        cur = x
        while True:
            if cur in hidden:
                anc_cache[x] = None
                return None
            p = m.parent(cur)
            if p is None:
                break
            out.append(p)
            cur = p
        anc_cache[x] = out
        return out

    for r in m.relations:
        if r.kind not in (USE, PROVIDE, ALLOCATE) or default_init_edge(m, r):
            continue
        s, d = alias.get(r.src, r.src), alias.get(r.dst, r.dst)
        if s == d or not m.has(s) or not m.has(d):
            continue
        cs, cd = chain(s), chain(d)
        if cs is None or cd is None:
            continue
        ds = set(cd)
        common = next((a for a in cs if a in ds), None)
        if common is None or common in (s, d):
            continue
        lca[common] += r.count
        if m.parent(s) == common and m.parent(d) == common and is_file(m, s) and is_file(m, d):
            loose[common] += r.count
    return dict(lca), dict(loose)


def drawn_parent(m: Model, node: str, expanded: set[str]) -> str | None:
    """The unfolded container a drawn node sits in (``files:C`` sits in C, ``body:F`` in F)."""
    if node.startswith(("files:", "body:")):
        return owner_of(node)
    p = m.parent(node)
    while p is not None:
        if p in expanded:
            return None if m.entities[p].attrs.get("root") else p
        p = m.parent(p)
    return None


def bundle(m: Model, edges: list["RolledEdge"], expanded: set[str], min_fan: int = 3) -> list["RolledEdge"]:
    """Fan-collapse: edges from one node to >= min_fan children of the same unfolded
    container are drawn as one edge to the container; symmetrically for sources.
    Edges inside a container are left alone."""
    def is_inside(node: str, container: str) -> bool:
        x: str | None = owner_of(node)
        while x is not None:
            if x == container:
                return True
            x = m.parent(x)
        return False

    def pass_(edges: list[RolledEdge], side: str) -> list[RolledEdge]:
        groups: dict[tuple, list[RolledEdge]] = defaultdict(list)
        for e in edges:
            end = e.dst if side == "dst" else e.src
            other = e.src if side == "dst" else e.dst
            c = drawn_parent(m, end, expanded)
            if e.kind == PROVIDE or c is None or is_inside(other, c):  # which module realises which interface is the point; never bundled
                groups[("keep", id(e))].append(e)
            else:
                groups[(side, other, c, e.kind, e.subkind)].append(e)
        out: list[RolledEdge] = []
        merged_edges: list[tuple[str, str, str, str, RolledEdge]] = []  # (other, container, kind, sub, edge)
        for key, es in groups.items():
            if key[0] == "keep" or len({(e.dst if side == "dst" else e.src) for e in es}) < min_fan:
                out.extend(es)
                continue
            _, other, c, kind, sub = key
            merged = RolledEdge(kind, other if side == "dst" else c, c if side == "dst" else other, sub, 0, "exact")
            for e in es:
                merged.count += e.count
                merged.via |= e.via
                if CONF_RANK[e.confidence] > CONF_RANK[merged.confidence]:
                    merged.confidence = e.confidence
            merged.via.add(f"fan:{len(es)}")
            merged_edges.append((other, c, kind, sub, merged))
        # a bundle that attaches to a container's boundary absorbs every edge from deeper
        # inside that container to the same other end, so one target is not reached both
        # from the boundary and from a grandchild
        for other, c, kind, sub, merged in merged_edges:
            rest = []
            for e in out:
                end = e.dst if side == "dst" else e.src
                o = e.src if side == "dst" else e.dst
                if e.kind == kind and e.subkind == sub and o == other and end != c and is_inside(end, c):
                    merged.count += e.count
                    merged.via |= e.via
                else:
                    rest.append(e)
            out = rest
        return out + [me for *_, me in merged_edges]

    return sorted(pass_(pass_(edges, "dst"), "src"), key=lambda e: (e.src, e.dst, e.kind, e.subkind))


def rollup(m: Model, visible: set[str], hidden: set[str] = frozenset(), through_interfaces: bool = True,
         dangling_use_kinds=DANGLING_USE_KINDS, dangling_provide_kinds=DANGLING_PROVIDE_KINDS,
         annotate_folded_groups: bool = True, env_edges: bool = False, shown_groups=frozenset()) -> RolledUp:
    """``shown_groups``: root groups the viewer draws as boxes (folded or not); edges to them
    are drawn, and interfaces inside them are not derived through. Other root groups are
    filtered out of the picture: their edges become annotations."""
    alias = image_aliases(m)
    rep = representative(m, visible, hidden, alias)
    edges: dict[tuple, RolledEdge] = {}
    internal: dict[str, int] = defaultdict(int)
    hidden_count = 0
    unresolved: dict[str, int] = defaultdict(int)

    def add(kind: str, a: str, b: str, sub: str, count: int, conf: str, via: str | None = None) -> None:
        if kind == PROVIDE:
            a, b = b, a  # drawn from the interface to what realises it, so a reader follows client -> API -> implementation
        if a == b or owner_of(a) == owner_of(b):
            internal[owner_of(a)] += count
            return
        if sub == "ref":
            sub = "import"  # references are the fine-grained form of imports; one edge kind in the picture
        key = (kind, a, b, sub)
        e = edges.get(key)
        if e is None:
            e = edges[key] = RolledEdge(kind, a, b, sub, 0, conf)
        e.count += count
        if CONF_RANK[conf] > CONF_RANK[e.confidence]:
            e.confidence = conf
        if via:
            e.via.add(via)

    users: dict[str, list] = defaultdict(list)
    providers: dict[str, list] = defaultdict(list)
    for r in m.relations:
        if r.kind not in (USE, PROVIDE, ALLOCATE) or default_init_edge(m, r):
            continue
        dst_ent = m.entities[r.dst]
        if dst_ent.kind == "unresolved":
            if rep(r.src) is not None:
                unresolved[dst_ent.attrs.get("language") or dst_ent.id.split(":")[1]] += r.count
            continue
        if r.subkind == "runs_as" and r.src in alias:
            continue
        if through_interfaces and dst_ent.kind in DERIVE_KINDS and r.dst not in visible:
            g = rep(r.dst)
            # derive through the interface unless its group is drawn: then the group, route
            # group or variable is a node and call and handler attach to it directly
            if g is None or (m.entities[g].kind == "group" and m.parent(g) is None and g not in shown_groups):
                (users if r.kind == USE else providers)[r.dst].append(r)
                continue
        a, b = rep(r.src), rep(r.dst)
        if a is None or b is None:
            hidden_count += r.count
            continue
        add(r.kind, a, b, r.subkind, r.count, r.confidence)

    for iface in set(users) | set(providers):
        us, ps = users.get(iface, []), providers.get(iface, [])
        sub = m.entities[iface].kind
        if us and ps:
            for u in us:
                a = rep(u.src)
                if a is None:
                    hidden_count += u.count
                    continue
                for p in ps:
                    b = rep(p.src)
                    if b is None:
                        continue
                    add(USE, a, b, sub, u.count, "heuristic" if "heuristic" in (u.confidence, p.confidence) else "exact", iface)
        else:
            g = rep(iface)
            keep = (us if sub in dangling_use_kinds else []) + (ps if sub in dangling_provide_kinds else [])
            for r in keep:
                a = rep(r.src)
                if a is None or g is None:
                    hidden_count += r.count
                    continue
                add(r.kind, a, g, sub + "-dangling", r.count, r.confidence, iface)

    # Edges touching a filtered-out group become annotations on the other end; a code node copied
    # into a drawn service gets a chip instead of an edge; env wiring (reader -> setter) says
    # the same as allocation and is suppressed unless asked for.
    annotations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    allocations: dict[str, list[str]] = defaultdict(list)
    suppressed: dict[str, int] = defaultdict(int)
    kept: list[RolledEdge] = []
    def root_group(x: str) -> bool:
        return m.has(x) and m.entities[x].kind == "group" and m.parent(x) is None and x not in shown_groups

    for e in edges.values():
        sg, dg = root_group(e.src), root_group(e.dst)
        if annotate_folded_groups and (sg or dg) and not (sg and dg):
            node, group = (e.dst, e.src) if sg else (e.src, e.dst)
            annotations[node][group] += e.count
        elif e.kind == ALLOCATE and e.subkind == "copies" and m.has(e.dst) and m.entities[e.dst].kind == "service":
            allocations[e.src].append(e.dst)
        elif e.subkind == "env" and not env_edges:
            suppressed["env"] += e.count
        else:
            kept.append(e)

    return RolledUp(sorted(visible), sorted(kept, key=lambda e: (e.src, e.dst, e.kind, e.subkind)),
                  dict(internal), {k: dict(v) for k, v in annotations.items()},
                  {k: sorted(v) for k, v in allocations.items()}, hidden_count, dict(unresolved), dict(suppressed))
