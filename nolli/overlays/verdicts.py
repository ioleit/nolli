"""``verdicts``: what a review said about a change's boxes and edges.

A review agent answers on the review diagram (``nolli/export/brief.py``): each verdict
targets a node id of that Mermaid diagram (``n_…``), or an edge ``n_a --> n_b``. Read, a
verdict names what it is about by address (``ends``: one node, or the two ends of an edge),
and that is how it is kept: the ``verdicts`` map of the change (``StoredMaps``), by target
(a node's address, or ``"<a> --> <b>"``), with who made them (``about``: ``{"by": "agent" |
"ci" | "upload", …}``). Any projection of the change then shows each verdict on the drawn box
that stands for its target, or on the drawn edge that carries the edge between its two ends.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from ..model import address
from ..scene import Element, MapStore, Scene, StoredMaps
from .change import find_change_scene, find_review_cut, format_name, list_ancestors_to_unfold  # noqa: F401
from ..transform.graph import Graph

# least attention first: praise asks for more than "fine", a doubt more than praise (the viewer
# shows the mark of a box's highest-ranked verdict). The brief offers fine / good / question / problem;
# brilliant and blunder are not offered yet.
VERDICTS = ("fine", "good", "brilliant", "question", "problem", "blunder")
VERDICT_RANK = {v: i for i, v in enumerate(VERDICTS)}
TARGET = re.compile(r"^\s*(n_\w+)\s*(?:-+>|-\.->|==>)?\s*(n_\w+)?\s*$")
EDGE = re.compile(r"^\s*(?P<src>n_\w+)\s*(?:-->|-\.->|==>)(?:\|(?P<label>[^|]*)\|)?\s*(?P<dst>n_\w+)\s*$")
NODE = re.compile(r"^\s*(?:subgraph\s+)?(n_\w+)\s*[\[(]")
PAIR = " --> "


def find_diagram_ids(scene: Scene) -> dict[str, str]:
    """A diagram's node ids back to what they stand for: nodes (an unfolded one is a subgraph,
    ``<id>_g``), and a module's own code (``<file>#``) or a directory's."""
    out = {}
    for x, kind in scene.model.kinds.items():
        out[address.format_identifier(x)] = out[address.format_identifier(x) + "_g"] = x
        if kind in ("file", "directory"):
            out[address.format_identifier(address.own_code(x))] = address.own_code(x)
    return out


def format_target(ends: list[str]) -> str:
    return PAIR.join(ends)


def read_verdicts(text: str, ids: dict[str, str]) -> list[dict]:
    """verdicts.json: a list (or {"verdicts": [...]}) of {target, verdict, reason}, ``target`` a
    diagram node id or ``n_a --> n_b``. Numbered V1, V2, … in file order, each with what it is
    about (``ends``, addresses); raises ValueError on a bad file."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("verdicts", [])
    if not isinstance(data, list):
        raise ValueError("expected a list of {target, verdict, reason}")
    out = []
    for i, v in enumerate(data, 1):
        g = TARGET.match(v["target"]) if isinstance(v, dict) and isinstance(v.get("target"), str) else None
        if g is None or v.get("verdict") not in VERDICTS:
            raise ValueError(f"bad verdict {v!r}: needs a target (a diagram node id, or 'n_a --> n_b') and a verdict in {VERDICTS}")
        ends = [ids[t] for t in g.groups() if t] if all(t in ids for t in g.groups() if t) else []
        out.append({"id": f"V{i}", "target": v["target"].strip(), "verdict": v["verdict"], "reason": str(v.get("reason", "")), "ends": ends})
    return out


def check_verdicts(scene: Scene, mermaid: str, text: str) -> tuple[list[dict], list[str]]:
    """verdicts.json checked against the review diagram it answers: the verdicts read, and what
    is wrong with them, each problem a sentence an agent can act on. Stricter than
    ``read_verdicts``: every target must be a box or an edge drawn in that diagram, and every
    verdict needs a reason."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"not valid JSON: {exc}"]
    if isinstance(data, dict):
        data = data.get("verdicts")
    if not isinstance(data, list):
        return [], ['expected a JSON list of {"target", "verdict", "reason"} (or {"verdicts": [...]})']
    ids = find_diagram_ids(scene)
    nodes = {g.group(1) for line in mermaid.splitlines() if (g := NODE.match(line))}
    edges = {(ids.get(g["src"]), ids.get(g["dst"])) for line in mermaid.splitlines() if (g := EDGE.match(line))}
    errors, ok = [], []
    for i, v in enumerate(data, 1):
        where = f"verdict {i}"
        if not isinstance(v, dict):
            errors.append(f"{where}: not an object")
            continue
        target, verdict, reason = v.get("target"), v.get("verdict"), v.get("reason")
        where = f"verdict {i} ({target!r})"
        if verdict not in VERDICTS:
            errors.append(f"{where}: verdict is {verdict!r}, must be one of {', '.join(VERDICTS)}")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{where}: needs a reason (a non-empty string)")
        g = TARGET.match(target) if isinstance(target, str) else None
        if not g:
            errors.append(f"{where}: target must be a node id of the diagram (n_…) or an edge 'n_a --> n_b'")
            continue
        missing = [t for t in g.groups() if t and t not in nodes]
        if missing:
            errors.append(f"{where}: {', '.join(missing)} is not a node of the diagram")
            continue
        if g.group(2):
            a, b = ids[g.group(1)], ids[g.group(2)]
            if (a, b) not in edges:
                hint = " (the diagram has it the other way round)" if (b, a) in edges else ""
                errors.append(f"{where}: the diagram draws no edge from {g.group(1)} to {g.group(2)}{hint}")
                continue
        ok.append(v)
    if not data:
        errors.append("no verdicts: give at least one")
    return (read_verdicts(json.dumps(ok), ids) if ok else []), errors


# -- kept by target ------------------------------------------------------------------


def dump_verdicts(verdicts: list[dict]) -> dict[str, dict]:
    """Verdicts as the ``verdicts`` map keeps them: by target (address, or ``a --> b``)."""
    out: dict[str, dict] = defaultdict(lambda: {"verdicts": []})
    for v in verdicts:
        if v.get("ends"):
            out[format_target(v["ends"])]["verdicts"].append({k: v[k] for k in ("id", "target", "verdict", "reason")})
    return dict(out)


def parse_verdicts(values) -> list[dict]:
    """The kept verdicts as a list in their order (V1, V2, …), each with its ``ends``."""
    out = [{**v, "ends": k.split(PAIR)} for k, rec in values.items() if isinstance(k, str) for v in rec.get("verdicts", [])]
    return sorted(out, key=lambda v: int(v["id"][1:]))


def describe_verdicts(scene: Scene, verdicts: list[dict]) -> list[dict]:
    """Each verdict with what the viewer says about it: ``edge``, ``reveal`` (what to unfold
    to see its target) and ``label`` (how the diagram names it)."""
    g = scene.memo(Graph)
    out = []
    for v in verdicts:
        ends = v.get("ends") or []
        name = lambda x: format_name(g, address.owner(x)) + (" (own code)" if address.is_own_code(x) else "")  # noqa: E731
        reveal: list[str] = []
        for x in ends:
            if not g.has(address.owner(x)):
                continue
            for a in list_ancestors_to_unfold(g, address.owner(x)) + ([address.owner(x)] if address.is_own_code(x) else []):
                if a not in reveal:
                    reveal.append(a)
        out.append(v | {"ends": ends, "edge": len(ends) == 2, "reveal": reveal,
                        "label": " → ".join(name(x) for x in ends) if ends else v["target"]})
    return out


def find_provenance(maps: MapStore, key) -> dict | None:
    """Who made a change's verdicts (its ``verdicts`` map's ``about``); None without verdicts.
    ``key``: the change's key (``nolli/keys.py``)."""
    stored = maps.open_maps("verdicts")
    return (stored.load_about(key.format_key()) or {"by": "unknown"}) if stored.load(key.format_key()) else None


def save_verdicts(maps: MapStore, key, verdicts: list[dict], by: dict) -> None:
    """Make ``verdicts`` (read, so with their ``ends``) the change's, with where they came from."""
    import time

    at = by.get("at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    maps.open_maps("verdicts").save(key.format_key(), dump_verdicts(verdicts), {**by, "at": at})


def keep_verdicts_file(maps: MapStore, key, scene: Scene, text: str, by: dict) -> list[dict]:
    """A verdicts.json (on the review diagram's ids) read and kept as the change's verdicts."""
    verdicts = read_verdicts(text, find_diagram_ids(scene))
    save_verdicts(maps, key, verdicts, by)
    return verdicts


class Verdicts:
    """A change's own elements: the verdicts kept on them. A drawn box or edge: the verdicts
    that land on it (``place_verdicts``), and the worst of them."""

    name = "verdicts"

    def __init__(self, stored: StoredMaps) -> None:
        self.stored = stored

    def load(self, scene: Scene) -> list[dict]:
        return parse_verdicts(self.stored.load(scene.model.key))

    def load_about(self, scene: Scene) -> dict | None:
        return self.stored.load_about(scene.model.key)

    def place(self, scene: Scene) -> dict:
        """Where this review's verdicts land in a projected scene (once per scene)."""
        change = find_change_scene(next(iter(scene.list_nodes()), None)) if scene.model.kinds else None
        return place_verdicts(scene, describe_verdicts(change, self.load(change))) if change is not None else {}

    def compute(self, el: Element) -> dict | None:
        if "source" not in el.scene.sources:
            own = self.stored.load(el.model_key).get(el.key) if not el.is_edge else None
            return {"verdicts": own["verdicts"]} if own else None
        change = find_change_scene(el)
        if change is None:
            return None
        placed = el.scene.memo(self.place)
        vs = placed.get(el.key) or []
        if not vs:
            return {"verdicts": [], "verdict": None, "own": [], "inside": []}
        ends = [el.key.src, el.key.dst] if el.is_edge else [el.key]
        own = [v for v in vs if [address.owner(x) for x in v["ends"]] == ends]
        return {"verdicts": [v["id"] for v in vs], "verdict": max((v["verdict"] for v in vs), key=VERDICT_RANK.get),
                "own": own, "inside": [v for v in vs if v not in own]}


def find_drawn(scene: Scene):
    """base node -> the drawn box that stands for it, the innermost one."""
    order = scene.model.meta.get("projection", {}).get("order", [])
    inv: dict[str, str] = {}
    for n in order:
        for k in scene.list_refs("source", n):
            inv[k] = n
    for n in order:
        inv[n] = n
    return inv


def place_verdicts(scene: Scene, verdicts: list[dict]) -> dict:
    """Where each verdict is shown in a projected scene: on the drawn box that stands for its
    target, or on the drawn edge that carries the edge between its two ends. Roll-up and fan
    bundling both draw an edge between boxes that contain its ends (a folded box, or the
    unfolded box a bundle ends at), so that is the drawn edge between the boxes around the two
    ends, the innermost there is. An edge that fell inside one box lands on that box; one
    drawn as a badge, on its source."""
    inv = find_drawn(scene)
    drawn = set(scene.model.kinds)
    parent = {x: scene.model.find_parent(x) for x in drawn}

    def drawn_box(x: str) -> str | None:
        r = x if x in drawn else inv.get(x) or inv.get(address.owner(x))
        return r if r in drawn else None

    def around(x: str) -> list[str]:
        out = [x]
        while parent.get(out[-1]) in drawn:
            out.append(parent[out[-1]])
        return out

    edge_at: dict[tuple[str, str], object] = {}
    for e in scene.model.list_edges():
        edge_at.setdefault((e.src, e.dst), e)

    def carrier(a: str, b: str):
        for s, d in ((a, b), (b, a)):
            found = [(i + j, edge_at[(x, y)]) for i, x in enumerate(around(s)) for j, y in enumerate(around(d)) if (x, y) in edge_at]
            if found:
                return min(found, key=lambda f: f[0])[1]
        return None

    on: dict = defaultdict(list)
    for v in verdicts:
        ends = [drawn_box(x) for x in v["ends"]]
        if not ends or None in ends:
            continue
        if len(ends) == 2 and ends[0] != ends[1]:
            e = carrier(ends[0], ends[1])
            if e is not None:
                on[e].append(v)
                continue
        on[ends[0]].append(v)
    return dict(on)
