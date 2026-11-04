"""``review``: what a review shows on a drawn box or edge of a change: which of the numbered
changes it carries (``items``) and the second part of its hover card, under a line: what
happened to it and the verdicts on it (``card``, Markdown)."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..model import EdgeKey
from ..prose import code, count, listing, mark, one_line, paragraphs, say
from ..scene import Element, Scene
from .change import find_change_scene, list_changes
from .verdicts import VERDICT_RANK, find_drawn

FULL_VERDICTS = 3  # a card lists this many verdicts in full; beyond, it sums up those from inside


def place_items(scene: Scene) -> dict:
    """Which numbered changes each drawn box and edge shows. An item about facts lands on the
    edges carrying those facts; an item about nodes (or facts that fell inside one box) on the
    box that stands for them."""
    change = find_change_scene(scene.list_nodes()[0]) if scene.model.kinds else None
    if change is None:
        return {}
    items = list_changes(change)
    inv = find_drawn(scene)
    drawn = set(scene.model.kinds)
    by_fact: dict[str, list[str]] = defaultdict(list)
    for it in items:
        for f in it["facts"]:
            by_fact[f].append(it["id"])
    out: dict = defaultdict(set)
    placed: set[str] = set()
    for e in scene.list_edges():
        ids = {i for f in (e.get("projection") or {}).get("changed", []) for i in by_fact.get(f, [])}
        out[e.key] |= ids
        placed |= ids
    for it in items:
        if it["id"] in placed and it["category"] == "dependency":
            continue
        for x in it["focus"]:
            r = x if x in drawn else inv.get(x)
            if r is not None and r in drawn:
                out[r].add(it["id"])
                break
    return {k: sorted(v, key=lambda i: int(i[1:])) for k, v in out.items()}


def list_verdict_lines(verdicts: list[dict], inside: list[dict] = (), items: int = 0, where: str = "") -> list[str]:
    """One line per verdict, worst first: [!] reason. Verdicts rolled up from inside a folded box
    or bundled edge (``inside``) become one line that counts them like the review panel does,
    "15 changes in this directory · verdicts: [??] 1 [✓] 8", once there are more than a card
    lists (FULL_VERDICTS in all) and more than one of them."""
    if len(verdicts) + len(inside) <= FULL_VERDICTS or len(inside) < 2:
        verdicts, inside = [*verdicts, *inside], []
    lines = [mark(v["verdict"]) + " " + one_line(v["reason"]) for v in sorted(verdicts, key=lambda v: -VERDICT_RANK[v["verdict"]])]
    if inside:
        n = Counter(v["verdict"] for v in inside)
        line = say(f"{{}} {where} · verdicts:", count(items, "change")) if items else f"Verdicts {where}:"
        for v in sorted(n, key=lambda v: -VERDICT_RANK[v]):
            line += " " + mark(v) + " " + code(n[v])
        lines.insert(0, line)
    return lines


def describe_box(nd: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0, kind: str = "box") -> str:
    """Sentences on a box's hover card in a review: what happened to it, the verdicts on it, and
    those on what it holds (``inside``, summed up when there are several; ``items`` changes)."""
    lines: list[str] = []
    if nd:
        lines_changed = nd["added"] or nd["removed"]
        where = f" in {count(nd['files'], 'file')}" if nd["files"] > 1 else ""
        if nd["status"] == "added":
            lines.append(say("New in this change" + (", {} lines" if lines_changed else ""), *([f"+{nd['added']}"] if lines_changed else [])))
        elif nd["status"] == "removed":
            lines.append(say("Removed in this change" + (", {} lines" if lines_changed else ""), *([f"−{nd['removed']}"] if lines_changed else [])))
        elif lines_changed:
            lines.append(say("Changed: {} {} lines" + where, f"+{nd['added']}", f"−{nd['removed']}"))
        elif not (nd.get("new") or nd.get("gone")) and nd.get("internal"):
            lines.append(say("Changed: {} between what it holds", count(nd["internal"], "dependency", "dependencies")))
        if (nd.get("new") or nd.get("gone")) and nd["status"] == "changed":
            bits = ([count(nd["new"], "new part")] if nd.get("new") else []) + ([count(nd["gone"], "removed part")] if nd.get("gone") else [])
            lines.append(say("Inside: " + " and ".join("{}" for _ in bits), *bits))
        if nd.get("old_path"):
            lines.append(say("Renamed from {}", nd["old_path"]))
        if nd.get("renamed_to"):
            lines.append(say("Renamed to {}", nd["renamed_to"]))
    return paragraphs(lines + list_verdict_lines(verdicts, inside, items, f"in this {kind}"))


def describe_edge(names: dict[str, str], ed: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0) -> str:
    """Sentences on top of an edge's hover card in a review: how its references changed, which
    names came or went, and the verdicts on it and on the dependencies it bundles."""
    lines: list[str] = []
    if ed:
        if ed["status"] == "added":
            lines.append(say("New in this change: {}", count(ed["head"], "reference")))
        elif ed["status"] == "removed":
            lines.append(say("Removed in this change: {} gone", count(ed["base"], "reference")))
        else:
            lines.append(say("Changed: {} → {} references", ed["base"], ed["head"]))
        came = sorted({names.get(c["dst"], c["dst"]) for c in ed.get("changes", []) if c["status"] == "added"})
        went = sorted({names.get(c["dst"], c["dst"]) for c in ed.get("changes", []) if c["status"] == "removed"})
        if came:
            lines.append(listing("Now uses {}", came, "more", limit=4))
        if went:
            lines.append(listing("No longer uses {}", went, "more", limit=4))
    return paragraphs(lines + list_verdict_lines(verdicts, inside, items, "on this edge"))


class Review:
    name = "review"

    def compute(self, el: Element) -> dict | None:
        if "source" not in el.scene.sources or find_change_scene(el) is None:
            return None
        if el.is_edge and el.kind == "contain":
            return None
        items = el.scene.memo(place_items).get(el.key, [])
        v = el.get("verdicts") or {}
        own, inside = v.get("own", []), v.get("inside", [])
        if el.is_edge:
            change = find_change_scene(el)
            ed = el.get("change")
            names = {c["dst"]: (change.find_element(c["dst"]).get("facts") or {}).get("name", c["dst"]) if change.has(c["dst"]) else c["dst"]
                     for c in (ed or {}).get("changes", [])}
            card = describe_edge(names, ed, own, inside, len(items))
        else:
            kind = el.kind if el.kind != "anchor" else "box"
            card = describe_box(el.get("change"), own, inside, len(items), kind.replace("_", " "))
        return {"items": items, "card": card}


__all__ = ["EdgeKey", "Review", "place_items"]
