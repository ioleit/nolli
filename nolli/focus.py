"""Focus: a set of entities the reader wants to understand, and how they connect.

Given focus members (any entities, at any depth), ``connect`` finds how they are
connected in the fact graph, below any roll-up, so a folded box cannot fake a path:

- for each ordered pair of members, every shortest *directed* path from one to the other,
  following ``use`` and ``provide`` in the picture's direction (an interface points to what
  realises it), up to ``MAX_HOPS`` steps;
- for a pair with no directed path either way, the nearest shared dependency or shared
  user (the meeting point with the smallest sum of distances).

Everything inside a member counts as the member, so a focused directory connects through
all of its files. The entities on these paths are *connectors*; the fact edges along them
are *path facts*. ``focus_cut`` turns this into the containers to unfold: the ancestors of
each member (always), then those of the connectors, as far as the budget allows.
``relevance`` grades what a scene draws: ``focus``, ``path``, ``near`` (one edge from a
focus box) or ``context`` (the rest, drawn faded).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .model import PROVIDE, USE, Model
from .rollup import DERIVE_KINDS, default_init_edge, visible_from_expanded

MAX_HOPS = 6  # longest connection looked for, in fact edges
MAX_MEET = 3  # shared dependencies or users kept per unconnected pair


@dataclass
class Connections:
    members: list[str]  # the focus members that exist in the model
    connectors: dict[str, int] = field(default_factory=dict)  # entity -> length of the shortest connection it is on
    facts: set[tuple[str, str]] = field(default_factory=set)  # (src, dst) fact edges on a connection, picture direction
    pairs: dict[tuple[str, str], str] = field(default_factory=dict)  # (a, b) -> "path", "meet" or "none"


def _owner(m: Model, members: set[str]):
    """Maps an entity to the focus member containing it (or itself)."""
    cache: dict[str, str] = {}

    def own(x: str) -> str:
        if x in cache:
            return cache[x]
        cur: str | None = x
        while cur is not None and cur not in members:
            cur = m.parent(cur)
        cache[x] = cur if cur is not None else x
        return cache[x]

    return own


def _graph(m: Model, members: set[str], hidden: set[str]):
    """Forward and backward adjacency of the fact graph, members collapsed."""
    own = _owner(m, members)
    cut: dict[str, bool] = {}

    def gone(x: str) -> bool:  # hidden by role, itself or through an ancestor
        if x not in cut:
            p = m.parent(x)
            cut[x] = x in hidden or (p is not None and gone(p))
        return cut[x]

    fwd: dict[str, set[str]] = defaultdict(set)
    back: dict[str, set[str]] = defaultdict(set)
    for r in m.relations:
        if r.kind not in (USE, PROVIDE) or default_init_edge(m, r):
            continue
        if m.entities[r.dst].kind == "unresolved" or gone(r.src) or gone(r.dst):
            continue
        a, b = (r.dst, r.src) if r.kind == PROVIDE else (r.src, r.dst)  # interface -> what realises it
        a, b = own(a), own(b)
        if a != b:
            fwd[a].add(b)
            back[b].add(a)
    return fwd, back


def _bfs(adj: dict[str, set[str]], start: str, members: set[str], limit: int) -> dict[str, int]:
    """Distances from start, not walking through other members (a path ends at the first one)."""
    dist = {start: 0}
    q = deque([start])
    while q:
        v = q.popleft()
        if dist[v] >= limit or (v != start and v in members):
            continue
        for w in adj.get(v, ()):
            if w not in dist:
                dist[w] = dist[v] + 1
                q.append(w)
    return dist


def connect(m: Model, focus, hidden: set[str] = frozenset(), max_hops: int = MAX_HOPS) -> Connections:
    members = sorted({f for f in focus if m.has(f)})
    out = Connections(members)
    if len(members) < 2:
        return out
    ms = set(members)
    fwd, back = _graph(m, ms, hidden)
    down = {s: _bfs(fwd, s, ms, max_hops) for s in members}  # s -> everything it reaches
    up = {s: _bfs(back, s, ms, max_hops) for s in members}  # s -> everything reaching it

    def keep(v: str, d: int) -> None:
        if v not in ms:
            out.connectors[v] = min(d, out.connectors.get(v, d))

    for i, a in enumerate(members):
        for b in members[i + 1:]:
            found = False
            for s, t in ((a, b), (b, a)):
                d = down[s].get(t)
                if d is None:
                    continue
                found = True
                # v is on a shortest s -> t path when the distances add up; the same for edges
                on = {v for v, dv in down[s].items() if dv + up[t].get(v, max_hops + 1) == d}
                for v in on:
                    keep(v, d)
                    for w in fwd.get(v, ()):
                        if w in on and down[s][v] + 1 + up[t].get(w, max_hops + 1) == d:
                            out.facts.add((v, w))
            if found:
                out.pairs[(a, b)] = "path"
                continue
            # no path: the nearest thing both use, or both are used by
            best: list[tuple[int, str, str]] = []
            for side, dist in (("down", down), ("up", up)):
                for v in dist[a].keys() & dist[b].keys():
                    if v not in ms:
                        best.append((dist[a][v] + dist[b][v], side, v))
            if not best:
                out.pairs[(a, b)] = "none"
                continue
            best.sort()
            lo = best[0][0]
            if lo > max_hops:
                out.pairs[(a, b)] = "none"
                continue
            out.pairs[(a, b)] = "meet"
            for d, side, v in [x for x in best if x[0] == lo][:MAX_MEET]:
                dist = down if side == "down" else up
                for s in (a, b):
                    # walk back from the meeting point to s along decreasing distance
                    layer = {v}
                    while layer:
                        nxt = set()
                        for x in layer:
                            keep(x, d)
                            for y in (back if side == "down" else fwd).get(x, ()):
                                if dist[s].get(y) == dist[s][x] - 1:
                                    out.facts.add((y, x) if side == "down" else (x, y))
                                    if y != s:
                                        nxt.add(y)
                        layer = nxt
    return out


def chain_of(m: Model, x: str) -> list[str]:
    """The containers to unfold so that x is drawn as its own box (its ancestors)."""
    return list(m.ancestors(x))


def focus_cut(m: Model, c: Connections, hidden: set[str], budget: int, start: set[str]) -> set[str]:
    """``start`` plus the ancestors of each member, then of each connector (nearest
    connections first) while the picture stays within ``budget`` boxes."""
    expanded = set(start)
    for f in c.members:
        expanded.update(chain_of(m, f))
    size = len(visible_from_expanded(m, expanded, hidden))
    for v in sorted(c.connectors, key=lambda v: (c.connectors[v], v)):
        if not m.has(v):
            continue
        more = set(chain_of(m, v)) - expanded
        if not more:
            continue
        n = len(visible_from_expanded(m, expanded | more, hidden))
        if n <= max(budget, size):
            expanded |= more
            size = n
    return expanded


def relevance(m: Model, c: Connections, rep, nodes: list[dict], edges: list[dict]) -> dict:
    """Grades the drawn nodes and edges in place (``relevance``) and returns a summary.
    ``rep`` maps an entity to the drawn node standing for it (or None)."""
    by_id = {n["id"]: n for n in nodes}

    def drawn_up(x: str | None) -> list[str]:  # a drawn node and the drawn containers around it
        out = []
        while x is not None and x in by_id:
            out.append(x)
            x = by_id[x]["parent"]
        return out

    focus_boxes = {r for f in c.members if (r := rep(f)) is not None}
    path_boxes = {r for v in c.connectors if m.has(v) and (r := rep(v)) is not None
                  and not by_id[r].get("container")} - focus_boxes
    # drawn (src, dst) pairs a path fact may have become: either end may be bundled to a container
    pairs: set[tuple[str, str]] = set()
    steps = set(c.facts)
    for (u, i) in c.facts:  # caller -> interface -> handler is one edge when derived through
        if m.has(i) and m.entities[i].kind in DERIVE_KINDS:
            steps |= {(u, h) for (j, h) in c.facts if j == i}
    for u, v in steps:
        ru, rv = rep(u), rep(v)
        if ru is None or rv is None or ru == rv:
            continue
        pairs |= {(a, b) for a in drawn_up(ru) for b in drawn_up(rv)}

    near: set[str] = set()
    for e in edges:
        if (e["src"], e["dst"]) in pairs:
            e["relevance"] = "path"
            for x in (e["src"], e["dst"]):
                if x not in focus_boxes and not by_id.get(x, {}).get("container"):  # a bundled end: its box is a frame
                    path_boxes.add(x)
        elif e["src"] in focus_boxes or e["dst"] in focus_boxes:
            e["relevance"] = "near"
            near |= {e["src"], e["dst"]}
        else:
            e["relevance"] = "context"
    near -= focus_boxes | path_boxes
    holds: set[str] = set()  # containers around something relevant are a frame, not context
    for x in focus_boxes | path_boxes | near:
        holds.update(drawn_up(by_id[x]["parent"]) if x in by_id else ())
    for n in nodes:
        x = n["id"]
        n["relevance"] = ("focus" if x in focus_boxes else "path" if x in path_boxes else "near" if x in near
                          else "frame" if x in holds else "context")
    return {
        "members": c.members, "drawn": sorted(focus_boxes), "connectors": len(c.connectors),
        "pairs": [{"a": a, "b": b, "how": how} for (a, b), how in sorted(c.pairs.items())],
    }
