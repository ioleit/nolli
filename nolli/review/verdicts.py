"""Reads, checks, keeps and places a review's verdicts on a change."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping

from nolli.model.model import Key
from nolli.model.scene import Element, Overlay, Scene, StoredMaps
from nolli.model.schema import EntityId
from nolli.review import changes
from nolli.review.reading import Reader
from nolli.store.maps import MapStore

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
    for x, kind in ((n.id, n.kind) for n in scene.model.entities.values()):
        out[x.as_identifier()] = out[x.as_identifier() + "_g"] = x
        if kind in ("file", "directory"):
            out[x.own_code().as_identifier()] = x.own_code()
    return out


def _format_target(ends: list[str]) -> str:
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


def _dump_verdicts(verdicts: list[dict]) -> dict[str, dict]:
    """Verdicts as the ``verdicts`` map keeps them: by target (address, or ``a --> b``)."""
    out: dict[str, dict] = defaultdict(lambda: {"verdicts": []})
    for v in verdicts:
        if v.get("ends"):
            out[_format_target(v["ends"])]["verdicts"].append({k: v[k] for k in ("id", "target", "verdict", "reason")})
    return dict(out)


def parse_verdicts(values) -> list[dict]:
    """The kept verdicts as a list in their order (V1, V2, …), each with its ``ends``."""
    out = [{**v, "ends": [EntityId(x) for x in k.split(PAIR)]} for k, rec in values.items() if isinstance(k, str) for v in rec.get("verdicts", [])]
    return sorted(out, key=lambda v: int(v["id"][1:]))


def describe_verdicts(scene: Scene, verdicts: list[dict]) -> list[dict]:
    """Each verdict with what the viewer says about it: ``edge``, ``reveal`` (what to unfold
    to see its target) and ``label`` (how the diagram names it)."""
    g = Reader(scene)
    out = []
    for v in verdicts:
        ends = [EntityId(x) for x in v.get("ends") or []]
        name = lambda x: changes.format_name(g, x.owner) + (" (own code)" if x.is_own_code else "")
        reveal: list[str] = []
        for x in ends:
            if not g.has(x.owner):
                continue
            for a in changes.list_ancestors_to_unfold(g, x.owner) + ([x.owner] if x.is_own_code else []):
                if a not in reveal:
                    reveal.append(a)
        out.append(v | {"ends": ends, "edge": len(ends) == 2, "reveal": reveal,
                        "label": " → ".join(name(x) for x in ends) if ends else v["target"]})
    return out


def find_provenance(maps: MapStore, key) -> dict | None:
    """Who made a change's verdicts (its ``verdicts`` map's ``about``); None without verdicts.
    ``key``: the change's key (``nolli/model/keys.py``)."""
    stored = maps.open_maps("verdicts")
    return (stored.load_about(key.format_key()) or {"by": "unknown"}) if stored.load(key.format_key()) else None


def save_verdicts(maps: MapStore, key, verdicts: list[dict], by: dict) -> None:
    """Make ``verdicts`` (read, so with their ``ends``) the change's, with where they came from.

    They are kept in the change's ``verdicts`` map (``StoredMaps``), by target (a node's
    address, or ``"<a> --> <b>"``), with who made them as its ``about``
    (``{"by": "agent" | "upload", …}``)."""
    import time

    at = by.get("at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    maps.open_maps("verdicts").save(key.format_key(), _dump_verdicts(verdicts), {**by, "at": at})


def _keep_verdicts_file(maps: MapStore, key, scene: Scene, text: str, by: dict) -> list[dict]:
    """A verdicts.json (on the review diagram's ids) read and kept as the change's verdicts."""
    verdicts = read_verdicts(text, find_diagram_ids(scene))
    save_verdicts(maps, key, verdicts, by)
    return verdicts


class Verdicts(Overlay):
    """On a change's node: the verdicts that start there (on the node, on its own code, or on an
    edge from it), as kept. On a drawn box or edge: the verdicts that land on it, from what it
    and what it holds stand for (``land``), and the worst of them. Any projection of the change
    shows each verdict on the drawn box that stands for its target, or on the drawn edge that
    carries the edge between its two ends."""

    name = "verdicts"

    def __init__(self, stored: StoredMaps) -> None:
        self.stored = stored

    def load(self, scene: Scene) -> list[dict]:
        return parse_verdicts(self.stored.load(scene.model.key.format_key()))

    def load_about(self, scene: Scene) -> dict | None:
        return self.stored.load_about(scene.model.key.format_key())

    def group(self, scene: Scene) -> dict[str, list[dict]]:
        """A change's verdicts by the node their first end belongs to."""
        out: dict[str, list[dict]] = defaultdict(list)
        for v in self.load(scene):
            if v.get("ends"):
                out[v["ends"][0].owner].append(v)
        return dict(out)

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """For the change itself, the verdicts starting at each node. A scene drawn from the
        change computes them element by element, and a scene of a single commit has none."""
        if "source" in scene.sources:
            return Overlay.compute_all(self, scene) if "change" in scene.model.meta else {}
        return {x: {"starting": vs} for x, vs in self.group(scene).items()}

    def compute(self, el: Element) -> dict | None:
        if "source" not in el.scene.sources:
            return el.scene.read_all(self.name).get(el.key)
        if el.is_edge and el.kind == "contain":
            return None
        roots = [el.src, el.dst] if el.is_edge else [el.parent or el] if el.kind == "anchor" else [el]
        seen, vs = set(), []
        for v in (v for r in roots for b in _iter_boxes(r) for s in b.sources.get("source", ())
                  for v in (s.get(self.name) or {}).get("starting", [])):
            if v["id"] not in seen and _land(el.scene, v) == el.key:
                seen.add(v["id"])
                vs.append(v)
        vs.sort(key=lambda v: int(v["id"][1:]))
        if not vs:
            return {"verdicts": [], "verdict": None, "own": [], "inside": []}
        ends = [el.key.src, el.key.dst] if el.is_edge else [el.key]
        own = [v for v in vs if [x.owner for x in v["ends"]] == ends]
        return {"verdicts": [v["id"] for v in vs], "verdict": max((v["verdict"] for v in vs), key=VERDICT_RANK.get),
                "own": own, "inside": [v for v in vs if v not in own]}


def _iter_boxes(el: Element):
    """A drawn box and every box drawn inside it."""
    yield el
    for c in el.children:
        yield from _iter_boxes(c)


def find_box(scene: Scene, x: str) -> str | None:
    """The drawn box that stands for ``x``: itself if drawn (a module's own code too), else the one
    holding it (or its owner). ``x`` may be text, as a change item's ``focus`` holds addresses."""
    x = EntityId(x)
    if scene.has(x):
        return x
    return scene.find_holder(x) or scene.find_holder(x.owner)


def _land(scene: Scene, v: dict) -> object | None:
    """The drawn box or edge a verdict is shown on.

    A verdict on a node is shown on the box that stands for the node. A verdict on an edge is
    shown on the drawn edge between the innermost boxes around its two ends, since roll-up and
    fan bundling draw an edge between boxes that contain its ends (a folded box, or the
    unfolded box a bundle ends at). An edge that falls inside one box is shown on that box,
    and one drawn as a badge on its source."""
    ends = [find_box(scene, x) for x in v["ends"]]
    if not ends or None in ends:
        return None
    if len(ends) == 2 and ends[0] != ends[1]:
        e = _find_carrier(scene, ends[0], ends[1])
        if e is not None:
            return e
    return ends[0]


def _find_carrier(scene: Scene, a: str, b: str):
    """The drawn edge that carries an edge from a to b (drawn boxes), or from b to a."""
    def around(x: str) -> list[str]:
        out = [x]
        while (p := scene.model.find_parent(out[-1])) is not None:
            out.append(p)
        return out

    for s, d in ((a, b), (b, a)):
        found = [(i + j, e) for i, x in enumerate(around(s)) for j, y in enumerate(around(d))
                 for e in scene.model.list_out(x) if e.dst == y]
        if found:
            return min(found, key=lambda f: f[0])[1]
    return None
