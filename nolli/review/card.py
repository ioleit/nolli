"""Sums up changes and verdicts on each drawn box and edge, as the second part of its hover card."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from nolli.logic.formats.descriptions import prose
from nolli.model.model import Key
from nolli.model.scene import Element, Overlay, Scene
from nolli.review import verdicts as verdicts_lib
from nolli.store.maps import MapStore

FULL_VERDICTS = 3  # a card lists this many verdicts in full; beyond, it sums up those from inside


def _list_verdict_lines(verdicts: list[dict], inside: list[dict] = (), where: str = "") -> list[str]:
    """One line per verdict, worst first: [!] reason. Verdicts rolled up from inside a folded box
    or bundled edge (``inside``) become one line that counts them, "Verdicts in this directory:
    [??] 1 [✓] 8", once there are more than a card lists (FULL_VERDICTS in all) and more than
    one of them."""
    if len(verdicts) + len(inside) <= FULL_VERDICTS or len(inside) < 2:
        verdicts, inside = [*verdicts, *inside], []
    lines = [prose.mark(v["verdict"]) + " " + prose.one_line(v["reason"]) for v in sorted(verdicts, key=lambda v: -verdicts_lib.VERDICT_RANK[v["verdict"]])]
    if inside:
        n = Counter(v["verdict"] for v in inside)
        line = f"Verdicts {where}:"
        for v in sorted(n, key=lambda v: -verdicts_lib.VERDICT_RANK[v]):
            line += " " + prose.mark(v) + " " + prose.code(n[v])
        lines.insert(0, line)
    return lines


def _describe_box(nd: dict | None, verdicts: list[dict], inside: list[dict] = (), kind: str = "box") -> str:
    """Sentences on a box's hover card in a review: what happened to it, the verdicts on it, and
    those on what it holds (``inside``, summed up when there are several)."""
    lines: list[str] = []
    if nd:
        t, own = nd["totals"], nd.get("own") or {}
        added, removed = (t["added"], t["removed"]) if t["added"] or t["removed"] else (own.get("added", 0), own.get("removed", 0))
        lines_changed = added or removed  # a class's own lines are not in its totals: its file counts them
        where = f" in {prose.count(t['files'], 'file')}" if t["files"] > 1 else ""
        if nd["status"] == "added":
            lines.append(prose.say("New in this change" + (", {} lines" if lines_changed else ""), *([f"+{added}"] if lines_changed else [])))
        elif nd["status"] == "removed":
            lines.append(prose.say("Removed in this change" + (", {} lines" if lines_changed else ""), *([f"−{removed}"] if lines_changed else [])))
        elif lines_changed:
            lines.append(prose.say("Changed: {} {} lines" + where, f"+{added}", f"−{removed}"))
        elif not (t["new"] or t["gone"]) and nd.get("internal"):
            lines.append(prose.say("Changed: {} between what it holds", prose.count(nd["internal"], "dependency", "dependencies")))
        if (t["new"] or t["gone"]) and nd["status"] == "changed":
            bits = ([prose.count(t["new"], "new entity")] if t["new"] else []) + ([prose.count(t["gone"], "removed entity")] if t["gone"] else [])
            lines.append(prose.say("Inside: " + " and ".join("{}" for _ in bits), *bits))
        if own.get("old_path"):
            lines.append(prose.say("Renamed from {}", own["old_path"]))
        if own.get("renamed_to"):
            lines.append(prose.say("Renamed to {}", own["renamed_to"]))
    return prose.paragraphs(lines + _list_verdict_lines(verdicts, inside, f"in this {kind}"))


def _describe_edge(ed: dict | None, verdicts: list[dict], inside: list[dict] = (), names: dict[str, str] | None = None) -> str:
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
    return prose.paragraphs(lines + _list_verdict_lines(verdicts, inside, "on this edge"))


class Review(Overlay):
    """The ``review`` overlay. On a drawn box or edge of a change: the second part of its hover
    card, under a line: what happened to it and the verdicts on it (``card``, Markdown)."""

    name = "review"

    def compute(self, el: Element) -> dict | None:
        return el.scene.read_all(self.name).get(el.key)  # computed for the whole scene in compute_all

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """For a scene drawn from a change, the cards of its boxes and edges, built from that
        scene's verdicts and changes as a whole. A change itself, and a commit, have none."""
        if "change" not in scene.model.meta or "source" not in scene.sources:
            return {}
        named = scene.sources["source"].read_all("names")
        verdicts, changed = scene.read_all("verdicts"), scene.read_all("change")
        out: dict[Key, dict] = {}
        for e in scene.model.list_edges():
            el = scene.find_element(e.id)
            v = verdicts.get(e.id) or {}
            names = {s.relation.dst: named[s.relation.dst]["name"] for s in el.sources.get("source", ()) if s.is_edge}
            out[e.id] = {"card": _describe_edge(changed.get(e.id), v.get("own", []), v.get("inside", []), names)}
        for x, kind in ((n.id, n.kind) for n in scene.model.entities.values()):
            v = verdicts.get(x) or {}
            kind = kind if kind != "anchor" else "box"
            out[x] = {"card": _describe_box(changed.get(x), v.get("own", []), v.get("inside", []), kind.replace("_", " "))}
        return out


def make_overlays(maps: MapStore) -> list[Overlay]:
    """Make the overlays a review adds to a change's scene: its verdicts, kept in ``maps``, and
    the cards that show them. Pass them to ``operations.open_scene``."""
    return [verdicts_lib.Verdicts(maps.open_maps("verdicts")), Review()]
