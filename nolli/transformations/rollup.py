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
- Groups (the namespaces' roots: Artifacts, Configuration, Runtime, APIs) are a filter:
  a group the viewer shows is a box like any other, folded or unfolded; edges to
  a group it doesn't show are not drawn and become annotations on the other node
  ("uses 42 external packages"). Cross-cutting things are omnipresent; drawn,
  they hide the structure.

Interfaces (endpoints, env variables) are boxes in their group like anything else: a
call or a read ends on the endpoint or variable when its group is shown, and becomes an
annotation on the caller or reader when it is not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache

from nolli.model import address, metamodel
from nolli.transformations import reading
from nolli.transformations.reading import Reading

HIDDEN_ROLES_DEFAULT = ("test", "docs", "config", "data", "asset", "other")


@dataclass
class RolledEdge:
    kind: str
    src: str
    dst: str
    subkind: str
    count: int = 0
    confidence: str = "exact"
    via: set[str] = field(default_factory=set)
    names: dict[str, int] = field(default_factory=dict)  # referenced names (Python refs) -> references
    types: int = 0  # of count, references only to types (``type_only``): no dependency at run time
    # in a diff model (nolli/diff.py): references before and after, and the changed facts
    # underneath (fact key -> added / removed / changed); in a plain model base == head == count
    base: int = 0
    head: int = 0
    changed: dict[str, str] = field(default_factory=dict)
    # the drawn nodes a lifted line stands for at each end (lift()); empty: the ends themselves
    ends: dict[str, set[str]] = field(default_factory=lambda: {"src": set(), "dst": set()})
    members: set = field(default_factory=set)  # the edges of the source it stands for

    @property
    def status(self) -> str | None:
        """added / removed / changed, or None when nothing underneath changed."""
        if self.base == 0 and self.head > 0:
            return "added"
        if self.head == 0 and self.base > 0:
            return "removed"
        if self.changed or self.base != self.head:
            return "changed"
        return None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "src": self.src, "dst": self.dst, "subkind": self.subkind, "count": self.count,
                "confidence": self.confidence, "via": sorted(self.via), "names": dict(self.names), "types": self.types,
                "base": self.base, "head": self.head, "changed": dict(sorted(self.changed.items())),
                "ends": {"src": sorted(self.ends["src"] or {self.src}), "dst": sorted(self.ends["dst"] or {self.dst})}}

    def absorb(self, other: RolledEdge) -> None:
        """Count, interfaces, names, confidence and changes of an edge folded into this one."""
        self.count += other.count
        self.types += other.types
        self.via |= other.via
        for k, v in other.names.items():
            self.names[k] = self.names.get(k, 0) + v
        self.base += other.base
        self.head += other.head
        self.changed.update(other.changed)
        self.members |= other.members
        for side in ("src", "dst"):
            self.ends[side] |= other.ends[side] or {getattr(other, side)}
        if reading.CONF_RANK[other.confidence] > reading.CONF_RANK[self.confidence]:
            self.confidence = other.confidence


@dataclass
class RolledUp:
    visible: list[str]
    edges: list[RolledEdge]
    internal: dict[str, int]
    annotations: dict[str, dict[str, int]]  # node -> folded group -> count
    allocations: dict[str, list[str]]  # code node -> drawn services it is copied into (chips, not edges)
    hidden_relations: int
    unresolved: dict[str, int]
    internal_changes: dict[str, dict[str, str]] = field(default_factory=dict)  # box -> changed facts inside it
    annotation_changes: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)  # node -> group -> changed facts

    def to_dict(self) -> dict:
        return {"visible": self.visible, "edges": [e.to_dict() for e in self.edges], "internal": self.internal,
                "annotations": self.annotations, "allocations": self.allocations,
                "hidden_relations": self.hidden_relations, "unresolved": self.unresolved}


def image_aliases(g: Reading) -> dict[str, str]:
    """image -> service, for images that run as exactly one service (drawn as that service)."""
    runs: dict[str, list[str]] = defaultdict(list)
    for r in g.relations:
        if r.kind == metamodel.ALLOCATE and r.subkind == "runs_as":
            runs[r.src].append(r.dst)
    return {img: svcs[0] for img, svcs in runs.items() if len(svcs) == 1}


def hidden_by_role(g: Reading, roles=HIDDEN_ROLES_DEFAULT) -> set[str]:
    """Files whose role is hidden, and directories whose role is hidden unless a file with a
    shown role is somewhere inside: a directory's role is by bytes, so ``evals/`` full of
    result files is "config" while its Python modules are source, and those must stay."""
    rs = set(roles)
    keep: set[str] = set()
    for x, kind in g.nodes.items():
        if kind == "file" and g.file(x).get("role") not in rs:
            p = g.parent(x)
            while p is not None and p not in keep:
                keep.add(p)
                p = g.parent(p)
    return {x for x, kind in g.nodes.items() if kind in ("file", "directory") and g.file(x).get("role") in rs
            and not g.file(x).get("root") and x not in keep}


def chain_end(g: Reading, n: str, hidden: set[str]) -> str:
    """Follow single-child directory chains (``frontend/playground/src``) to their end.

    A directory whose only non-hidden content is one subdirectory adds a level without
    adding structure; the chain is drawn as one node labelled with the joined path.
    """
    while g.kind(n) == "directory":
        kids = [c for c in g.children(n) if c not in hidden and not is_init(g, c)]
        if len(kids) == 1 and g.kind(kids[0]) == "directory":
            n = kids[0]
        else:
            break
    return n


def _is_file(g: Reading, x: str) -> bool:
    return g.kind(x) == "file"


def is_init(g: Reading, x: str) -> bool:
    """A package body (Python's ``__init__.py``, a TypeScript ``index.ts``; the file tree
    marks it ``body``): the folder's own code, not a module of its own."""
    return g.has(x) and g.kind(x) == "file" and bool(g.file(x).get("body"))


def _under(g: Reading, x: str, d: str) -> bool:
    """x (or the file owning x) lies in directory d's subtree."""
    cur: str | None = x
    while cur is not None:
        if cur == d:
            return True
        cur = g.parent(cur)
    return False


def _default_init_edge(g: Reading, r) -> bool:
    """An edge from a package body to something inside its own package: the re-export /
    submodule import every ``__init__`` has. Not a dependency; dropped from the picture."""
    if not is_init(g, r.src):
        return False
    d = g.parent(r.src)
    return d is not None and _under(g, r.dst, d)


def init_anchor_dirs(g: Reading, hidden: set[str]) -> set[str]:
    """Directories whose ``__init__`` body still has edges after the default ones are
    dropped: these get an anchor node when unfolded, the others get nothing."""
    out: set[str] = set()
    drawn_kinds = ("directory", "file", *metamodel.SYMBOL_KINDS, "service", "image")  # badge targets (externals, env, endpoints) don't count
    for r in g.relations:
        if r.kind not in (metamodel.USE, metamodel.PROVIDE, metamodel.ALLOCATE) or _default_init_edge(g, r):
            continue
        for end, other in ((r.src, r.dst), (r.dst, r.src)):
            if is_init(g, end) and end not in hidden and g.has(other) and g.kind(other) in drawn_kinds:
                d = g.parent(end)
                if d is not None:
                    out.add(d)
    return out


def _file_degree(g: Reading, hidden: set[str]) -> dict[str, int]:
    """Relations touching each leaf entity, for ranking which files to show first."""
    deg: dict[str, int] = defaultdict(int)
    for r in g.relations:
        if r.kind in (metamodel.USE, metamodel.PROVIDE, metamodel.ALLOCATE) and r.src not in hidden and r.dst not in hidden:
            deg[r.src] += r.count
            deg[r.dst] += r.count
    return dict(deg)


def _shown_files(g: Reading, d: str, hidden: set[str], k: int, degree: dict[str, int]) -> tuple[list[str], list[str]]:
    """The k most connected files of d (ties by name), and the rest."""
    loose = sorted((c for c in g.children(d) if c not in hidden and _is_file(g, c)),
                   key=lambda c: (-degree.get(c, 0), c))
    return loose[:k], loose[k:]


def visible_from_expanded(g: Reading, expanded: set[str], hidden: set[str], show_files=None, degree=None,
                          walked: set[str] | None = None) -> set[str]:
    """The cut for a set of unfolded containers.

    An unfolded container shows *all* its non-hidden children (a partially opened
    container hides problems); children that are unfolded themselves are walked. An
    unfolded module also gets ``<module>#`` (address.own_code), the anchor for its own code. Single-child
    directory chains are compressed to their end, so ``expanded`` should name chain ends.
    A package's ``__init__`` is not a node: its classes are children of the package and its
    body is the package's anchor ``<dir>#``, present only if it has edges.
    ``show_files`` and ``degree`` are accepted for compatibility and ignored. ``walked``,
    if given, receives the unfolded containers actually reached (the effective unfold set).
    """
    visible: set[str] = set()
    anchors = init_anchor_dirs(g, hidden)

    def walk(n: str) -> None:
        if walked is not None:
            walked.add(n)
        kind = g.kind(n)
        if kind == "file":
            visible.add(address.own_code(n))
        if kind == "directory" and n in anchors:
            visible.add(address.own_code(n))
        for c in g.children(n):
            if c in hidden:
                continue
            if kind == "directory" and is_init(g, c):
                visible.update(cc for cc in g.children(c) if cc not in hidden)  # the package's own classes
                continue
            if kind == "directory" and g.kind(c) == "directory":
                c = chain_end(g, c, hidden)
            if c in expanded and g.children(c):
                walk(c)
            else:
                visible.add(c)

    for r in g.roots():
        if r in hidden:
            continue
        if r in expanded and g.children(r):
            walk(r)
        else:
            visible.add(r)
    return visible


def representative(g: Reading, visible: set[str], hidden: set[str], alias: dict[str, str] | None = None):
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
            if g.kind(cur) == "file" and address.own_code(cur) in visible:
                result = address.own_code(cur)  # the module is unfolded: its own code
                break
            if is_init(g, cur):
                p = g.parent(cur)
                if p is not None and address.own_code(p) in visible:
                    result = address.own_code(p)  # the package is unfolded: its own code
                    break
            cur = g.parent(cur)
        cache[x] = result
        return result

    return rep


def _fact_key(r) -> str:
    """An edge's id: ``kind|src|dst|subkind``."""
    return r.key.format()


def _owner_of(node: str) -> str:
    return address.owner(node)


def _top_of(g: Reading, node: str, expanded: set[str]) -> str:
    """The outermost drawn ancestor of a drawn node (the top-level component it belongs to)."""
    cur = node
    while True:
        p = drawn_parent(g, cur, expanded)
        if p is None:
            return cur
        cur = p


def utility_badges(g: Reading, rolled: RolledUp, min_in: int = 6) -> set[str]:
    """Boxes with more than ``min_in`` incoming dependency edges in the picture are
    utilities: all those edges become badges on their sources ("→ observability"), like
    externals, since a box that many arrows point at mostly adds clutter. Edges are counted
    as drawn (one per arrow, whatever its count); groups and what's inside them are never
    utilities, so a shown group keeps its edges. Returns the utility nodes."""
    def in_group(x: str) -> bool:
        cur: str | None = _owner_of(x)
        while cur is not None and g.has(cur):
            if g.kind(cur) == "group":
                return True
            cur = g.parent(cur)
        return False

    incoming: dict[str, int] = defaultdict(int)
    for e in rolled.edges:
        if e.kind == metamodel.USE:
            incoming[e.dst] += 1
    utilities = {d for d, n in incoming.items() if n > min_in and not in_group(d)}
    if not utilities:
        return utilities
    kept = []
    for e in rolled.edges:
        if e.kind == metamodel.USE and e.dst in utilities and not e.status:  # a changed dependency on a utility stays an edge, so a review sees it
            rolled.annotations.setdefault(e.src, {})
            rolled.annotations[e.src]["util:" + e.dst] = rolled.annotations[e.src].get("util:" + e.dst, 0) + e.count
        else:
            kept.append(e)
    rolled.edges = kept
    return utilities


def reveal_scores(g: Reading, hidden: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    """What unfolding reveals, per container.

    ``lca[C]`` counts relations whose endpoints lie in different children of C (their
    lowest common ancestor is C): exactly the edges that appear when C is unfolded.
    ``loose[C]`` counts relations between two loose files of C: the edges that appear
    when C's files node is opened. Relations touching hidden entities, or crossing
    between roots (code to an interface), do not count.
    """
    alias = image_aliases(g)
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
            p = g.parent(cur)
            if p is None:
                break
            out.append(p)
            cur = p
        anc_cache[x] = out
        return out

    for r in g.relations:
        if r.kind not in (metamodel.USE, metamodel.PROVIDE, metamodel.ALLOCATE) or _default_init_edge(g, r):
            continue
        s, d = alias.get(r.src, r.src), alias.get(r.dst, r.dst)
        if s == d or not g.has(s) or not g.has(d):
            continue
        cs, cd = chain(s), chain(d)
        if cs is None or cd is None:
            continue
        ds = set(cd)
        common = next((a for a in cs if a in ds), None)
        if common is None or common in (s, d):
            continue
        lca[common] += r.count
        if g.parent(s) == common and g.parent(d) == common and _is_file(g, s) and _is_file(g, d):
            loose[common] += r.count
    return dict(lca), dict(loose)


def drawn_parent(g: Reading, node: str, expanded: set[str]) -> str | None:
    """The unfolded container a drawn node sits in (the own code ``F#`` sits in F)."""
    if address.is_own_code(node):
        return _owner_of(node)
    p = g.parent(node)
    while p is not None:
        if p in expanded:
            return None if g.file(p).get("root") else p
        p = g.parent(p)
    return None


def lift(g: Reading, edges: list[RolledEdge], expanded: set[str], max_lines: int = 4) -> list[RolledEdge]:
    """Edge lifting: between any two drawn boxes A and B that are not one inside the other,
    at most ``max_lines`` lines are drawn, counting every line whose ends lie in A (or are A)
    and in B. A pair with more is fixed, finest pair first: its lines are lifted at the end
    with the wider spread (more distinct boxes) to that box, which merges them; if still too
    many, at the other end too. Lifting only ever makes lines coarser, so a lift never
    breaks a pair fixed before, and nothing finer is drawn under a lifted line. A provide
    edge (drawn interface -> module) is never lifted at the module's end: which module
    realises which interface is the point. A lifted line keeps the drawn nodes it stands for
    in ``ends``."""
    max_lines = max(1, max_lines)

    @cache
    def above(x: str) -> tuple[str, ...]:
        """x and its drawn containers, nearest first (own code sits in its module)."""
        out, p = [x], drawn_parent(g, x, expanded)
        while p is not None:
            out.append(p)
            p = drawn_parent(g, p, expanded)
        return tuple(out)

    lines = list(edges)
    for e in lines:
        for side in ("src", "dst"):
            e.ends[side] = e.ends[side] or {getattr(e, side)}

    def crossing(a: str, b: str) -> list[RolledEdge]:
        return [e for e in lines if a in above(e.src) and b in above(e.dst)]

    def merged(es: list[RolledEdge]) -> list[RolledEdge]:
        """Lines with the same ends and kind become one."""
        out: dict[tuple, RolledEdge] = {}
        for e in es:
            k = (e.kind, e.src, e.dst, e.subkind)
            if k in out:
                out[k].absorb(e)
            else:
                out[k] = e
        return list(out.values())

    done: set[tuple[str, str]] = set()  # pairs lifted as far as they go: never again (a pair of provide lines may stay over)
    while True:
        # every pair of disjoint drawn boxes a line passes between, with its lines
        pairs: dict[tuple[str, str], list[RolledEdge]] = defaultdict(list)
        for e in lines:
            up_s, up_d = above(e.src), above(e.dst)
            for a in up_s:
                for b in up_d:
                    if a != b and a not in up_d and b not in up_s:
                        pairs[(a, b)].append(e)
        bad = [(a, b) for (a, b), es in pairs.items() if len(es) > max_lines and (a, b) not in done]
        if not bad:
            break
        # the finest pair first: deepest, then the most crowded; names settle the rest
        a, b = max(bad, key=lambda ab: (len(above(ab[0])) + len(above(ab[1])), len(pairs[ab]), ab[0], ab[1]))
        done.add((a, b))
        es = pairs[(a, b)]
        spread_src, spread_dst = len({e.src for e in es}), len({e.dst for e in es})
        liftable_dst = [e for e in es if e.kind != metamodel.PROVIDE]
        order = ["dst", "src"] if spread_dst >= spread_src or not liftable_dst else ["src", "dst"]
        for side in order:
            for e in es:
                if side == "src":
                    e.src = a
                elif e.kind != metamodel.PROVIDE:
                    e.dst = b
                e.via.add("lift")
            rest = [e for e in lines if e not in es]
            es = merged(es)
            lines = rest + es
            if len(es) <= max_lines:
                break
    return sorted(lines, key=lambda e: (e.src, e.dst, e.kind, e.subkind))


def rollup(g: Reading, visible: set[str], hidden: set[str] = frozenset(),
         shown_groups=frozenset(), side: str = "both") -> RolledUp:
    """``shown_groups``: root groups the viewer draws as boxes (folded or not); edges to them
    are drawn. Other root groups are filtered out of the picture: their edges become
    annotations on the other end, and an edge between two of them is left out.
    ``side``: in a diff model, "base" or "head" draws one commit only; "both" draws the
    merged picture, with removed facts counted on their base side."""
    alias = image_aliases(g)
    rep = representative(g, visible, hidden, alias)
    edges: dict[tuple, RolledEdge] = {}
    internal: dict[str, int] = defaultdict(int)
    hidden_count = 0
    unresolved: dict[str, int] = defaultdict(int)

    internal_changes: dict[str, dict[str, str]] = defaultdict(dict)

    def add(kind: str, a: str, b: str, sub: str, count: int, conf: str, names: dict[str, int] | None = None,
            base: int | None = None, head: int | None = None, changed: dict[str, str] | None = None,
            types: int = 0, member=None) -> None:
        if kind == metamodel.PROVIDE:
            a, b = b, a  # drawn from the interface to what realises it, so a reader follows client -> API -> implementation
        if a == b or _owner_of(a) == _owner_of(b):
            internal[_owner_of(a)] += count
            if changed:
                internal_changes[_owner_of(a)].update(changed)
            return
        if sub == "ref":
            sub = "import"  # references are the fine-grained form of imports; one edge kind in the picture
        key = (kind, a, b, sub)
        e = edges.get(key)
        if e is None:
            e = edges[key] = RolledEdge(kind, a, b, sub, 0, conf)
        e.absorb(RolledEdge(kind, a, b, sub, count, conf, names=dict(names or {}), types=types, members={member} if member else set(),
                            base=count if base is None else base, head=count if head is None else head, changed=changed or {}))

    def sides(r) -> tuple[int, int, str | None]:
        """(references before, after, change status) of an edge; status None when unchanged."""
        d = r.diff
        if not d:
            return r.count, r.count, None
        return d.get("base", 0), d.get("head", 0), d["status"]

    def drawn_count(b: int, h: int) -> int:
        return b if side == "base" else h if side == "head" else (h or b)

    def on_side(r) -> bool:
        st = (r.diff or {}).get("status")
        return not ((side == "base" and st == "added") or (side == "head" and st == "removed"))

    for r in g.relations:
        if r.kind not in (metamodel.USE, metamodel.PROVIDE, metamodel.ALLOCATE) or _default_init_edge(g, r) or not on_side(r):
            continue
        if g.kind(r.dst) == "unresolved":
            if rep(r.src) is not None:
                unresolved[g.find_unresolved_language(r.dst)] += r.count
            continue
        if r.subkind == "runs_as" and r.src in alias:
            continue
        a, b = rep(r.src), rep(r.dst)
        if a is None or b is None:
            hidden_count += r.count
            continue
        rb, rh, st = sides(r)
        names: dict[str, int] = {}
        for f in r.references:
            if f.get("name"):
                names[f["name"]] = names.get(f["name"], 0) + f.get("count", 1)
        types = sum(f["count"] for f in r.references if f.get("type_only"))
        n = drawn_count(rb, rh)
        add(r.kind, a, b, r.subkind, n, r.confidence, names, rb, rh, {_fact_key(r): st} if st else None,
            min(types, n) if types and types >= r.count else types, r.key)

    # Edges touching a filtered-out group become annotations on the other end (or are left out
    # when both ends are such groups, e.g. environment set by deployment); a code node copied
    # into a drawn service gets a chip instead of an edge.
    annotations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    annotation_changes: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    allocations: dict[str, list[str]] = defaultdict(list)
    kept: list[RolledEdge] = []
    def root_group(x: str) -> bool:
        return g.has(x) and g.kind(x) == "group" and g.parent(x) is None and x not in shown_groups

    for e in edges.values():
        sg, dg = root_group(e.src), root_group(e.dst)
        if sg and dg:
            hidden_count += e.count
        elif sg or dg:
            node, group = (e.dst, e.src) if sg else (e.src, e.dst)
            annotations[node][group] += e.count
            if e.changed:
                annotation_changes[node][group].update(e.changed)
        elif e.kind == metamodel.ALLOCATE and e.subkind == "copies" and g.has(e.dst) and g.kind(e.dst) == "service":
            allocations[e.src].append(e.dst)
        else:
            kept.append(e)

    return RolledUp(sorted(visible), sorted(kept, key=lambda e: (e.src, e.dst, e.kind, e.subkind)),
                  dict(internal), {k: dict(v) for k, v in annotations.items()},
                  {k: sorted(v) for k, v in allocations.items()}, hidden_count, dict(unresolved),
                  {k: dict(v) for k, v in internal_changes.items()},
                  {k: {g: dict(c) for g, c in v.items()} for k, v in annotation_changes.items()})
