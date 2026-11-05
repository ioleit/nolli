"""``review``: what a review shows on a drawn box or edge of a change: which of the numbered
changes it carries (``items``) and the second part of its hover card, under a line: what
happened to it and the verdicts on it (``card``, Markdown)."""

from __future__ import annotations

from collections import Counter

from nolli.formats.descriptions import names as names_lib
from nolli.formats.descriptions import prose
from nolli.model.scene import Element
from nolli.review import changes
from nolli.review import verdicts as verdicts_lib

FULL_VERDICTS = 3  # a card lists this many verdicts in full; beyond, it sums up those from inside


def list_items(el: Element) -> dict[str, dict]:
    """Which numbered changes a change's node or edge is about (a node those that focus on it, an
    edge those whose facts it is), by id, with what placing them needs."""
    items = changes.list_changes(el.scene)
    mine = [it for it in items if (el.id in it["facts"] if el.is_edge else el.key in it["focus"])]
    return {it["id"]: {"category": it["category"], "focus": it["focus"]} for it in mine}


def place_items(el: Element) -> list[str]:
    """Which numbered changes a drawn box or edge shows. An item about facts lands on the edges
    carrying those facts; an item about nodes (or facts that fell inside one box, or became a
    badge) on the box that stands for the first of them that is drawn."""
    src = el.sources.get("source", ())
    if el.is_edge:
        return sorted({i for s in src for i in (s.get("review") or {}).get("about", {})}, key=lambda i: int(i[1:]))
    near = [*verdicts_lib.iter_boxes(el), *el.iter_ancestors()]  # a line lifted to a box around it carries what is inside too
    carried = {i for b in near for e in [*b.out_edges, *b.in_edges] for i in (e.get("review") or {}).get("items", [])}
    out = set()
    for s in src:
        for i, it in (s.get("review") or {}).get("about", {}).items():
            if it["category"] == "dependency" and i in carried:
                continue
            first = next((f for f in it["focus"] if verdicts_lib.find_box(el.scene, f) is not None), None)
            if first is not None and verdicts_lib.find_box(el.scene, first) == el.key:
                out.add(i)
    return sorted(out, key=lambda i: int(i[1:]))


def list_verdict_lines(verdicts: list[dict], inside: list[dict] = (), items: int = 0, where: str = "") -> list[str]:
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


def describe_box(nd: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0, kind: str = "box") -> str:
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
    return prose.paragraphs(lines + list_verdict_lines(verdicts, inside, items, f"in this {kind}"))


def describe_edge(ed: dict | None, verdicts: list[dict], inside: list[dict] = (), items: int = 0,
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
    return prose.paragraphs(lines + list_verdict_lines(verdicts, inside, items, "on this edge"))


class Review:
    name = "review"

    def compute(self, el: Element) -> dict | None:
        if el.is_edge and el.kind == "contain":
            return None
        if "change" not in el.meta:
            return None
        if "source" not in el.scene.sources:  # the change itself: which numbered changes are about it
            about = list_items(el)
            return {"items": sorted(about, key=lambda i: int(i[1:])), "about": about} if about else None
        items = place_items(el)
        v = el.get("verdicts") or {}
        own, inside = v.get("own", []), v.get("inside", [])
        ed = el.get("change")
        if el.is_edge:
            names = {s.key.dst: names_lib.format_name(s.dst) for s in el.sources.get("source", ()) if s.is_edge}
            card = describe_edge(ed, own, inside, len(items), names)
        else:
            kind = el.kind if el.kind != "anchor" else "box"
            card = describe_box(ed, own, inside, len(items), kind.replace("_", " "))
        return {"items": items, "card": card}
