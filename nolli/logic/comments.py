"""Comments: what people and agents say about the nodes and edges of a model, and where each
comment shows.

A comment is made while looking at one model, ``observed``: a projection of a change, a commit,
whatever was on screen. It is about one of that model's nodes or edges, ``target``, by its id, and
it means what it says there. An edge of a projection between two folded nodes stands for many
edges of the model it was drawn from, and a comment on it says nothing about any one of them.

A comment is kept on the model that ``observed`` is a view of (``keys.find_base_key``): a
comment on a projection of a change is kept on the change. Every scene drawn from that model
finds it there and shows it by address (``Comments``):

- on the node or edge with the same id, if the scene draws one;
- rolled up, if the scene folds it away: on the node drawn for a node folded into it, and on the
  edge drawn between the nodes that hold an edge's two ends;
- not at all, if the scene draws it finer than ``observed`` did, unfolded or split up. The comment
  was made on the whole, not on any of its parts.

A comment is a dict:

- ``id``: a short name unique among the comments of a model, "c-" and hex digits; the same
  comment on the same model has the same id wherever the repository is;
- ``observed``: the key of the model it was made on, as text;
- ``target``: the id of the node or edge of ``observed`` it is about;
- ``label``: how that node or edge is named, for a reader who cannot see ``observed``;
- ``by``: who made it ("agent", a person's name); a new batch from the same ``by`` on the same
  ``observed`` replaces the last one;
- ``at``: when, in ISO 8601;
- ``text``: what it says, in Markdown;
- ``verdict``: optionally, one of ``VERDICTS``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping

from nolli.logic.transformations.merge import reading
from nolli.model import keys, schema
from nolli.model.keys import ModelKey
from nolli.model.model import Key, Model
from nolli.model.scene import Element, Overlay, Scene
from nolli.model.schema import EntityId, RelationId

# least attention first: praise asks for more than "fine", a doubt more than praise. The review
# brief offers fine / good / question / problem; brilliant and blunder are not offered yet.
VERDICTS = ("fine", "good", "brilliant", "question", "problem", "blunder")
VERDICT_RANK = {v: i for i, v in enumerate(VERDICTS)}
ARROW = re.compile(r"^\s*(\S+?)\s*(-->|-\.->|==>)\s*(\S+)\s*$")
ARROW_SUBKINDS = {"-->": ("import",), "==>": ("http",), "-.->": ("import", "env", "deploy", "provide")}  # mermaid.STYLE_ARROW


# -- taking comments in -------------------------------------------------------------------


def _map_identifiers(model: Model) -> dict[str, EntityId]:
    """Map the identifier a Mermaid diagram gives each node (``EntityId.as_identifier``), and that
    of the subgraph it is drawn as when unfolded (with ``_g``), to the node's address."""
    out: dict[str, EntityId] = {}
    for x in model.entities:
        out[x.as_identifier()] = out[x.as_identifier() + "_g"] = x
    return out


def _resolve_target(model: Model, text: str, ids: Mapping[str, EntityId] | None = None) -> tuple[Key | None, str | None]:
    """Find the node or edge of ``model`` that ``text`` names, and return its id, or None and why
    not. Accept a node's address, an edge's id, a Mermaid node identifier (``ids``, from
    ``_map_identifiers``), and an edge written as ``a --> b`` (or ``==>``, ``-.->``) with either kind
    of node name on both sides."""
    ids = _map_identifiers(model) if ids is None else ids
    text = text.strip()
    try:
        key = schema.parse_key(text)
    except ValueError:
        key = None
    if key is not None and model.has(key):
        return key, None
    if isinstance(key, RelationId):
        return None, (f"{text} is no edge of this model ({model.key.format_key()}): comment on the model that draws it, "
                      "such as the projection it was seen in")
    m = ARROW.match(text)
    if m:
        a, b = _resolve_node(model, m[1], ids), _resolve_node(model, m[3], ids)
        missing = [t for t, x in ((m[1], a), (m[3], b)) if x is None]
        if missing:
            return None, f"{', '.join(missing)} is not a node of the diagram"
        edges = [e for e in model.list_out(a) if e.dst == b and e.kind != schema.RelationKind.CONTAIN]
        if not edges:
            back = any(e.dst == a for e in model.list_out(b))
            return None, f"the diagram draws no edge from {m[1]} to {m[3]}" + (" (it has it the other way round)" if back else "")
        styled = [e for e in edges if e.subkind in ARROW_SUBKINDS[m[2]]]
        return (styled or edges)[0].id, None
    x = _resolve_node(model, text, ids)
    if x is None:
        return None, f"{text} is not a node of the diagram (give a node's id, or an edge as 'a --> b')"
    return x, None


def _resolve_node(model: Model, text: str, ids: Mapping[str, EntityId]) -> EntityId | None:
    if text in ids:
        return ids[text]
    try:
        x = EntityId(text)
    except ValueError:
        return None
    return x if model.has(x) else None


def make_comments(scene: Scene, raw: str, by: str, *, verdict_needed: bool = False) -> tuple[list[dict], list[str]]:
    """Read comments made on ``scene``'s model from the JSON text ``raw`` and return them, with a
    problem for each that is wrong. ``raw`` is a list, or ``{"comments": [...]}`` (``"verdicts"``
    too), of ``{"target", "text", "verdict"}``; ``reason`` is read as ``text``. Each target is
    resolved to an id of the model (``_resolve_target``). With ``verdict_needed`` every comment
    needs a verdict. Each problem is a sentence an agent can act on."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], [f"not valid JSON: {exc}"]
    if isinstance(data, dict):
        data = data.get("comments", data.get("verdicts"))
    if not isinstance(data, list):
        return [], ['expected a JSON list of {"target", "verdict", "reason"} (or {"verdicts": [...]})']
    if not data:
        return [], ["no comments: give at least one"]
    ids, observed, at = _map_identifiers(scene.model), scene.model.key.format_key(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    repo = getattr(scene.model.key, "repo", None)
    where_in_repo = observed.removeprefix(repo.url) if repo else observed  # ids that do not depend on where a clone is
    g = reading.Reader(_find_root(scene))
    out, errors = [], []
    for i, v in enumerate(data, 1):
        if not isinstance(v, dict):
            errors.append(f"comment {i}: not an object")
            continue
        target, verdict, text = v.get("target"), v.get("verdict"), v.get("text", v.get("reason"))
        where = f"comment {i} ({target!r})"
        if verdict is not None or verdict_needed:
            if verdict not in VERDICTS:
                errors.append(f"{where}: verdict is {verdict!r}, must be one of {', '.join(VERDICTS)}")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{where}: needs a reason (a non-empty string)")
        if not isinstance(target, str):
            errors.append(f"{where}: target must be a node id of the diagram (n_…) or an edge 'n_a --> n_b'")
            continue
        key, why = _resolve_target(scene.model, target, ids)
        if key is None:
            errors.append(f"{where}: {why}")
            continue
        c = {"observed": observed, "target": str(key), "label": _label(g, key), "by": by, "at": at, "text": str(text or "").strip()}
        if verdict in VERDICTS:
            c["verdict"] = verdict
        c["id"] = "c-" + hashlib.sha1(f"{where_in_repo}|{by}|{key}|{c['text']}|{i}".encode()).hexdigest()[:10]
        out.append(c)
    return out, errors


def merge_comments(kept: list[dict], new: list[dict]) -> list[dict]:
    """Return ``kept`` with ``new`` in place of the comments of the same author on the same model."""
    replaced = {(c["observed"], c["by"]) for c in new}
    return [c for c in kept if (c.get("observed"), c.get("by")) not in replaced] + new


def _find_root(scene: Scene) -> Scene:
    """Return the scene ``scene`` was drawn from, through projections and selections."""
    while "source" in scene.sources:
        scene = scene.sources["source"]
    return scene


def _label(g: reading.Reader, key: Key) -> str:
    """Name a node or edge as a reader knows it: by path, an edge by the paths of its two ends."""
    if isinstance(key, RelationId):
        _kind, src, dst, _sub = key.split("|", 3)
        return f"{_name(g, EntityId(src))} → {_name(g, EntityId(dst))}"
    return _name(g, EntityId(key))


def _name(g: reading.Reader, x: EntityId) -> str:
    return reading.format_name(g, x.owner) + (" (own code)" if x.is_own_code else "")


# -- finding and showing them -----------------------------------------------------------


def _list_base_keys(scene: Scene) -> list[ModelKey]:
    """List the keys of the models whose comments ``scene`` shows: those it was drawn from, itself
    included, that are no view of another (``keys.find_base_key``). A projection of a change
    shows the change's comments and its two commits'."""
    out: list[ModelKey] = []
    todo = [scene]
    while todo:
        s = todo.pop(0)
        k = s.model.key
        if k is not None and keys.find_base_key(k) == k and k not in out:
            out.append(k)
        todo += list(s.sources.values())
    return out


class Comments(Overlay):
    """The comments that show on each drawn node and edge, as ``{"own": [...], "inside": [...]}``:
    ``own`` those about it, ``inside`` those rolled up onto it from what it holds or carries. It
    reads the comments with ``load`` (a store's ``load_comments``), from every model the scene was
    drawn from (``_list_base_keys``)."""

    name = "comments"
    cache_version = "1"

    def __init__(self, load: Callable[[ModelKey], list[dict]]) -> None:
        self.load = load

    def list_all(self, scene: Scene) -> list[dict]:
        """Return every comment kept on the models ``scene`` was drawn from, whether it shows on
        ``scene`` or not."""
        return [c for k in _list_base_keys(scene) for c in self.load(k)]

    def compute(self, el: Element) -> dict | None:
        return el.scene.read_all(self.name).get(el.key)  # computed for the whole scene in compute_all

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        out: dict[Key, dict] = {}
        for c in self.list_all(scene):
            placed = _place(scene, c)
            if placed is not None:
                key, own = placed
                out.setdefault(key, {"own": [], "inside": []})["own" if own else "inside"].append(c)
        return out


def _place(scene: Scene, comment: dict) -> tuple[Key, bool] | None:
    """Find the drawn node or edge a comment shows on, and whether it is its own target; return
    None if it does not show. See the module's docstring for the rules."""
    try:
        t = schema.parse_key(comment.get("target") or "")
    except ValueError:
        return None
    if scene.has(t):
        return t, True
    if isinstance(t, RelationId):
        kind, src, dst, subkind = t.split("|", 3)
        a, b = reading.find_box(scene, src), reading.find_box(scene, dst)
        if a is None or b is None:
            return None
        if a == b:
            return a, False
        e = _find_carrier(scene, a, b, kind, subkind)
        return (e, False) if e is not None else None
    x = reading.find_box(scene, t)
    return (x, False) if x is not None else None


def _find_carrier(scene: Scene, a: str, b: str, kind: str, subkind: str) -> Key | None:
    """Find the drawn edge of ``kind`` and ``subkind`` between the drawn nodes ``a`` and ``b``, or
    the nodes that hold them, either way round; the closest one, or None. Roll-up and fan bundling
    draw an edge between nodes that hold its ends. Only the nodes around ``a`` and ``b`` are
    searched, never those inside: an edge split up into edges to what an end holds has no carrier."""
    def around(x: str) -> list[str]:
        out = [x]
        while (p := scene.model.find_parent(out[-1])) is not None:
            out.append(p)
        return out

    for s, d in ((a, b), (b, a)):
        found = [(i + j, e) for i, x in enumerate(around(s)) for j, y in enumerate(around(d))
                 for e in scene.model.list_out(x) if e.dst == y and e.kind == kind and e.subkind == subkind]
        if found:
            return min(found, key=lambda f: f[0])[1].id
    return None


def describe_comments(scene: Scene, comments: list[dict]) -> list[dict]:
    """Return each comment with what it takes to go to it in ``scene``. ``ends`` are the addresses
    it is about that ``scene`` has, ``edge`` says whether it is about an edge, and ``reveal`` lists
    the nodes to unfold, outermost first, so that its ends are drawn."""
    g = reading.Reader(_find_root(scene))
    out = []
    for c in comments:
        try:
            t = schema.parse_key(c.get("target") or "")
        except ValueError:
            out.append({**c, "ends": [], "edge": False, "reveal": []})
            continue
        ends = [EntityId(p) for p in t.split("|", 3)[1:3]] if isinstance(t, RelationId) else [EntityId(t)]
        reveal: list[str] = []
        for x in ends:
            if not g.has(x.owner):
                continue
            for a in reading.list_ancestors_to_unfold(g, x.owner) + ([x.owner] if x.is_own_code else []):
                if a not in reveal:
                    reveal.append(a)
        out.append({**c, "ends": [x for x in ends if g.has(x.owner)], "edge": isinstance(t, RelationId), "reveal": reveal})
    return out
