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

from . import address
from .model import ALLOCATE, PROVIDE, SYMBOL_KINDS, USE, Model

CONF_RANK = {"exact": 0, "heuristic": 1, "statistical": 2}
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
                "base": self.base, "head": self.head, "changed": dict(sorted(self.changed.items()))}

    def absorb(self, other: "RolledEdge") -> None:
        """Count, interfaces, names, confidence and changes of an edge folded into this one."""
        self.count += other.count
        self.types += other.types
        self.via |= other.via
        for k, v in other.names.items():
            self.names[k] = self.names.get(k, 0) + v
        self.base += other.base
        self.head += other.head
        self.changed.update(other.changed)
        if CONF_RANK[other.confidence] > CONF_RANK[self.confidence]:
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


def image_aliases(m: Model) -> dict[str, str]:
    """image -> service, for images that run as exactly one service (drawn as that service)."""
    runs: dict[str, list[str]] = defaultdict(list)
    for r in m.relations:
        if r.kind == ALLOCATE and r.subkind == "runs_as":
            runs[r.src].append(r.dst)
    return {img: svcs[0] for img, svcs in runs.items() if len(svcs) == 1}


def hidden_by_role(m: Model, roles=HIDDEN_ROLES_DEFAULT) -> set[str]:
    """Files whose role is hidden, and directories whose role is hidden unless a file with a
    shown role is somewhere inside: a directory's role is by bytes, so ``evals/`` full of
    result files is "config" while its Python modules are source, and those must stay."""
    rs = set(roles)
    keep: set[str] = set()
    for e in m.entities.values():
        if e.kind == "file" and e.attrs.get("role") not in rs:
            p = m.parent(e.id)
            while p is not None and p not in keep:
                keep.add(p)
                p = m.parent(p)
    return {e.id for e in m.entities.values() if e.kind in ("file", "directory") and e.attrs.get("role") in rs
            and not e.attrs.get("root") and e.id not in keep}


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
    """A package body (Python's ``__init__.py``, a TypeScript ``index.ts``; the file tree
    marks it ``body``): the folder's own code, not a module of its own."""
    e = m.entities.get(x)
    return e is not None and e.kind == "file" and bool(e.attrs.get("body"))


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
    drawn_kinds = ("directory", "file", *SYMBOL_KINDS, "service", "image")  # badge targets (externals, env, endpoints) don't count
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
    unfolded module also gets ``<module>#`` (address.own_code), the anchor for its own code. Single-child
    directory chains are compressed to their end, so ``expanded`` should name chain ends.
    A package's ``__init__`` is not a node: its classes are children of the package and its
    body is the package's anchor ``<dir>#``, present only if it has edges.
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
            visible.add(address.own_code(n))
        if kind == "directory" and n in anchors:
            visible.add(address.own_code(n))
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
            if m.entities[cur].kind == "file" and address.own_code(cur) in visible:
                result = address.own_code(cur)  # the module is unfolded: its own code
                break
            if is_init(m, cur):
                p = m.parent(cur)
                if p is not None and address.own_code(p) in visible:
                    result = address.own_code(p)  # the package is unfolded: its own code
                    break
            cur = m.parent(cur)
        cache[x] = result
        return result

    return rep


def fact_key(r) -> str:
    """A stored relation's id: unique per (kind, src, dst, subkind) in a diff model."""
    return f"{r.kind}|{r.src}|{r.dst}|{r.subkind}"


def owner_of(node: str) -> str:
    return address.owner(node)


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
        if e.kind == USE and e.dst in utilities and not e.status:  # a changed dependency on a utility stays an edge, so a review sees it
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
    """The unfolded container a drawn node sits in (the own code ``F#`` sits in F)."""
    if address.is_own_code(node):
        return owner_of(node)
    p = m.parent(node)
    while p is not None:
        if p in expanded:
            return None if m.entities[p].attrs.get("root") else p
        p = m.parent(p)
    return None


def bundle(m: Model, edges: list["RolledEdge"], expanded: set[str], min_fan: int = 3) -> list["RolledEdge"]:
    """Fan-collapse: edges from one node to >= min_fan children of the same unfolded
    container are drawn as one edge to the container; symmetrically for sources (also for
    provide edges, whose source is the interface). Edges inside a container are left alone."""
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
            # which module realises which interface is the point: a provide edge (drawn interface ->
            # module) is never bundled at the module's end, but siblings in one unfolded API box
            # realised by the same module are one edge from the box
            if (e.kind == PROVIDE and side == "dst") or c is None or is_inside(other, c):
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
                merged.absorb(e)
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
                    merged.absorb(e)
                else:
                    rest.append(e)
            out = rest
        return out + [me for *_, me in merged_edges]

    return sorted(pass_(pass_(edges, "dst"), "src"), key=lambda e: (e.src, e.dst, e.kind, e.subkind))


def rollup(m: Model, visible: set[str], hidden: set[str] = frozenset(),
         shown_groups=frozenset(), side: str = "both") -> RolledUp:
    """``shown_groups``: root groups the viewer draws as boxes (folded or not); edges to them
    are drawn. Other root groups are filtered out of the picture: their edges become
    annotations on the other end, and an edge between two of them is left out.
    ``side``: in a diff model, "base" or "head" draws one commit only; "both" draws the
    merged picture, with removed facts counted on their base side."""
    alias = image_aliases(m)
    rep = representative(m, visible, hidden, alias)
    edges: dict[tuple, RolledEdge] = {}
    internal: dict[str, int] = defaultdict(int)
    hidden_count = 0
    unresolved: dict[str, int] = defaultdict(int)

    internal_changes: dict[str, dict[str, str]] = defaultdict(dict)

    def add(kind: str, a: str, b: str, sub: str, count: int, conf: str, name: str | None = None,
            base: int | None = None, head: int | None = None, changed: dict[str, str] | None = None,
            types: bool = False) -> None:
        if kind == PROVIDE:
            a, b = b, a  # drawn from the interface to what realises it, so a reader follows client -> API -> implementation
        if a == b or owner_of(a) == owner_of(b):
            internal[owner_of(a)] += count
            if changed:
                internal_changes[owner_of(a)].update(changed)
            return
        if sub == "ref":
            sub = "import"  # references are the fine-grained form of imports; one edge kind in the picture
        key = (kind, a, b, sub)
        e = edges.get(key)
        if e is None:
            e = edges[key] = RolledEdge(kind, a, b, sub, 0, conf)
        e.absorb(RolledEdge(kind, a, b, sub, count, conf, names={name: count} if name else {}, types=count if types else 0,
                            base=count if base is None else base, head=count if head is None else head, changed=changed or {}))

    def sides(r) -> tuple[int, int, str | None]:
        """(references before, after, change status) of a fact; status None when unchanged."""
        d = r.attrs.get("diff")
        if not d:
            return r.count, r.count, None
        return d.get("base", 0), d.get("head", 0), d["status"]

    def drawn_count(b: int, h: int) -> int:
        return b if side == "base" else h if side == "head" else (h or b)

    def on_side(r) -> bool:
        st = (r.attrs.get("diff") or {}).get("status")
        return not ((side == "base" and st == "added") or (side == "head" and st == "removed"))

    for r in m.relations:
        if r.kind not in (USE, PROVIDE, ALLOCATE) or default_init_edge(m, r) or not on_side(r):
            continue
        dst_ent = m.entities[r.dst]
        if dst_ent.kind == "unresolved":
            if rep(r.src) is not None:
                unresolved[dst_ent.attrs.get("language") or dst_ent.id.split(":")[1]] += r.count
            continue
        if r.subkind == "runs_as" and r.src in alias:
            continue
        a, b = rep(r.src), rep(r.dst)
        if a is None or b is None:
            hidden_count += r.count
            continue
        rb, rh, st = sides(r)
        add(r.kind, a, b, r.subkind, drawn_count(rb, rh), r.confidence, r.attrs.get("name"), rb, rh,
            {fact_key(r): st} if st else None, bool(r.attrs.get("type_only")))

    # Edges touching a filtered-out group become annotations on the other end (or are left out
    # when both ends are such groups, e.g. environment set by deployment); a code node copied
    # into a drawn service gets a chip instead of an edge.
    annotations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    annotation_changes: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    allocations: dict[str, list[str]] = defaultdict(list)
    kept: list[RolledEdge] = []
    def root_group(x: str) -> bool:
        return m.has(x) and m.entities[x].kind == "group" and m.parent(x) is None and x not in shown_groups

    for e in edges.values():
        sg, dg = root_group(e.src), root_group(e.dst)
        if sg and dg:
            hidden_count += e.count
        elif sg or dg:
            node, group = (e.dst, e.src) if sg else (e.src, e.dst)
            annotations[node][group] += e.count
            if e.changed:
                annotation_changes[node][group].update(e.changed)
        elif e.kind == ALLOCATE and e.subkind == "copies" and m.has(e.dst) and m.entities[e.dst].kind == "service":
            allocations[e.src].append(e.dst)
        else:
            kept.append(e)

    return RolledUp(sorted(visible), sorted(kept, key=lambda e: (e.src, e.dst, e.kind, e.subkind)),
                  dict(internal), {k: dict(v) for k, v in annotations.items()},
                  {k: sorted(v) for k, v in allocations.items()}, hidden_count, dict(unresolved),
                  {k: dict(v) for k, v in internal_changes.items()},
                  {k: {g: dict(c) for g, c in v.items()} for k, v in annotation_changes.items()})
