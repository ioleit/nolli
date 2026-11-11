"""Sums up changes and verdicts on each drawn box and edge."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping

from nolli.logic.formats.descriptions import prose
from nolli.model.model import Key, Relation
from nolli.model.scene import Element, Overlay, Scene
from nolli.review import changes
from nolli.review import verdicts as verdicts_lib

FULL_VERDICTS = 3  # a card lists this many verdicts in full; beyond, it sums up those from inside


def _index_items(scene: Scene) -> dict[Key, dict]:
    """Index the numbered changes (``changes.number_changes``) as the ``review`` overlay records
    them on the change itself. Each item is listed once, under the first node it focuses on
    (``listed``). Each node or edge also gets the ids of the items it is about (for a node those
    that focus on it, for an edge those whose facts it is), with what placing them needs
    (``items``, ``about``)."""
    about: dict[Key, dict[str, dict]] = {}
    listed: dict[Key, list[dict]] = {}
    for it in changes.number_changes(scene):
        what = {"category": it["category"], "focus": it["focus"]}
        for x in it["focus"]:
            about.setdefault(x, {})[it["id"]] = what
        for f in it["facts"]:
            about.setdefault(Relation.parse(f), {})[it["id"]] = what
        listed.setdefault(it["focus"][0], []).append(it)
    out = {k: {"items": sorted(v, key=lambda i: int(i[1:])), "about": v} for k, v in about.items()}
    for k, its in listed.items():
        out[k]["listed"] = its
    return out


def _place_edge_items(el: Element, about: Mapping[Key, dict]) -> list[str]:
    """Which numbered changes a drawn edge shows: those about the facts it carries (``about``:
    the ``review`` overlay of the change drawn)."""
    return sorted({i for s in el.sources.get("source", ()) for i in (about.get(s.key) or {}).get("about", {})}, key=lambda i: int(i[1:]))


def _place_box_items(scene: Scene, about: Mapping[Key, dict], on_edges: Mapping[Key, list[str]]) -> dict[str, list[str]]:
    """Which numbered changes each drawn box shows: those about nodes it stands for, and facts
    that fell inside it or became a badge, unless a line around it carries them (``on_edges``: a
    line lifted to a box around it carries what is inside too). Each box is asked about what
    it stands for; whether a line near it carries an item is read off the lines' ends."""
    ends: dict[str, set[str]] = defaultdict(set)  # item -> the drawn boxes at the ends of the lines carrying it
    for e, items in on_edges.items():
        for i in items:
            ends[i].update((e.src, e.dst))
    above: dict[str, set[str]] = {}  # a box and the boxes around it

    def around(x: str) -> set[str]:
        got = above.get(x)
        if got is None:
            got = above[x] = {x, *scene.model.iter_ancestors(x)}
        return got

    out: dict[str, list[str]] = {}
    for x in scene.model.entities:
        el, placed = scene.find_element(x), set()
        for s in el.sources.get("source", ()):
            for i, it in (about.get(s.key) or {}).get("about", {}).items():
                if it["category"] == "dependency" and any(b in around(x) or x in around(b) for b in ends.get(i, ())):
                    continue
                first = next((f for f in it["focus"] if verdicts_lib.find_box(scene, f) is not None), None)
                if first is not None and verdicts_lib.find_box(scene, first) == x:
                    placed.add(i)
        out[x] = sorted(placed, key=lambda i: int(i[1:]))
    return out


def _list_verdict_lines(verdicts: list[dict], inside: list[dict] = (), items: int = 0, where: str = "") -> list[str]:
    """One line per verdict, worst first: [!] reason. Verdicts rolled up from inside a folded box
    or bundled edge (``inside``) become one line that counts them like the review panel does,
    "15 changes in this directory · verdicts: [??] 1 [✓] 8", once there are more than a card
    lists (FULL_VERDICTS in all) and more than one of them."""
    if len(verdicts) + len(inside) <= FULL_VERDICTS or len(inside) < 2:
        verdicts, inside = [*verdicts, *inside], []
    lines = [prose.mark(v["verdict"]) + " " + prose.one_line(v["reason"]) for v in sorted(verdicts, key=lambda v: -verdicts_lib.VERDICT_RANK[v["verdict"]])]
    if inside:
        n = Counter(v["verdict"] for v in inside)
        line = prose.say(f"{{}} {where} · verdicts:", prose.count(items, "change")) if items else f"Verdicts {where}:"
        for v in sorted(n, key=lambda v: -verdicts_lib.VERDICT_RANK[v]):
            line += " " + prose.mark(v) + " " + prose.code(n[v])
        lines.insert(0, line)
    return lines


def _describe_box(nd: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0, kind: str = "box") -> str:
    """Sentences on a box's hover card in a review: what happened to it, the verdicts on it, and
    those on what it holds (``inside``, summed up when there are several; ``items`` changes)."""
    lines: list[str] = []
    if nd:
        lines_changed = nd["added"] or nd["removed"]
        where = f" in {prose.count(nd['files'], 'file')}" if nd["files"] > 1 else ""
        if nd["status"] == "added":
            lines.append(prose.say("New in this change" + (", {} lines" if lines_changed else ""), *([f"+{nd['added']}"] if lines_changed else [])))
        elif nd["status"] == "removed":
            lines.append(prose.say("Removed in this change" + (", {} lines" if lines_changed else ""), *([f"−{nd['removed']}"] if lines_changed else [])))
        elif lines_changed:
            lines.append(prose.say("Changed: {} {} lines" + where, f"+{nd['added']}", f"−{nd['removed']}"))
        elif not (nd.get("new") or nd.get("gone")) and nd.get("internal"):
            lines.append(prose.say("Changed: {} between what it holds", prose.count(nd["internal"], "dependency", "dependencies")))
        if (nd.get("new") or nd.get("gone")) and nd["status"] == "changed":
            bits = ([prose.count(nd["new"], "new part")] if nd.get("new") else []) + ([prose.count(nd["gone"], "removed part")] if nd.get("gone") else [])
            lines.append(prose.say("Inside: " + " and ".join("{}" for _ in bits), *bits))
        if nd.get("old_path"):
            lines.append(prose.say("Renamed from {}", nd["old_path"]))
        if nd.get("renamed_to"):
            lines.append(prose.say("Renamed to {}", nd["renamed_to"]))
    return prose.paragraphs(lines + _list_verdict_lines(verdicts, inside, items, f"in this {kind}"))


def _describe_edge(ed: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0,
                  names: dict[str, str] | None = None) -> str:
    """Sentences on top of an edge's hover card in a review: how its references changed, which
    names came or went, and the verdicts on it and on the dependencies it bundles."""
    lines: list[str] = []
    if ed:
        if ed["status"] == "added":
            lines.append(prose.say("New in this change: {}", prose.count(ed["head"], "reference")))
        elif ed["status"] == "removed":
            lines.append(prose.say("Removed in this change: {} gone", prose.count(ed["base"], "reference")))
        else:
            lines.append(prose.say("Changed: {} → {} references", ed["base"], ed["head"]))
        names = names or {}
        came = sorted({names.get(c["dst"], c["dst"]) for c in ed.get("changes", []) if c["status"] == "added"})
        went = sorted({names.get(c["dst"], c["dst"]) for c in ed.get("changes", []) if c["status"] == "removed"})
        if came:
            lines.append(prose.listing("Now uses {}", came, "more", limit=4))
        if went:
            lines.append(prose.listing("No longer uses {}", went, "more", limit=4))
    return prose.paragraphs(lines + _list_verdict_lines(verdicts, inside, items, "on this edge"))


class Review(Overlay):
    """The ``review`` overlay. On a change's node or edge: the numbered changes about it
    (``items``, ``about``). On a drawn box or edge of a change: which of the numbered changes it
    carries (``items``), and the second part of its hover card, under a line: what happened to
    it and the verdicts on it (``card``, Markdown)."""

    name = "review"

    def compute(self, el: Element) -> dict | None:
        return el.scene.read_all(self.name).get(el.key)  # computed for the whole scene in compute_all

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """For the change itself, the numbered changes each node and edge is about. For a scene
        drawn from the change, what each drawn edge carries, what each box shows, and the
        cards, built from that scene's verdicts and changes as a whole."""
        if "change" not in scene.model.meta:
            return {}
        if "source" not in scene.sources:
            return _index_items(scene)
        about, named = scene.sources["source"].read_all(self.name), scene.sources["source"].read_all("names")
        verdicts, changed = scene.read_all("verdicts"), scene.read_all("change")
        out: dict[Key, dict] = {}
        on_edges = {e: _place_edge_items(scene.find_element(e), about) for e in scene.model.list_edges()}
        for e, items in on_edges.items():
            el = scene.find_element(e)
            v = verdicts.get(e) or {}
            names = {s.key.dst: named[s.key.dst]["name"] for s in el.sources.get("source", ()) if s.is_edge}
            out[e] = {"items": items, "card": _describe_edge(changed.get(e), v.get("own", []), v.get("inside", []), len(items), names)}
        on_boxes = _place_box_items(scene, about, on_edges)
        for x, kind in ((n.id, n.kind) for n in scene.model.entities.values()):
            items = on_boxes[x]
            v = verdicts.get(x) or {}
            kind = kind if kind != "anchor" else "box"
            out[x] = {"items": items, "card": _describe_box(changed.get(x), v.get("own", []), v.get("inside", []), len(items), kind.replace("_", " "))}
        return out
