"""Writes the second part of a drawn node's or edge's hover card, under a line: what a change did
to it, and the comments that show on it (``nolli/logic/comments.py``)."""

from __future__ import annotations

from collections import Counter

from nolli.logic import comments as comments_lib
from nolli.logic.formats.descriptions import prose

FULL_COMMENTS = 3  # a card lists this many comments in full; beyond, it counts those from inside


def _rank(c: dict) -> int:
    return comments_lib.VERDICT_RANK.get(c.get("verdict") or "", -1)


def _list_comment_lines(own: list[dict], inside: list[dict] = (), where: str = "") -> list[str]:
    """Write one line per comment, most attention first: its verdict's mark, if it has one, and
    what it says. Once there are more comments than a card lists in full, and more than one of
    them rolled up from inside, write one line that counts those instead, "Comments in this
    directory: [??] 1 [✓] 8, 2 without a verdict"."""
    if len(own) + len(inside) <= FULL_COMMENTS or len(inside) < 2:
        own, inside = [*own, *inside], []
    lines = [(prose.mark(c["verdict"]) + " " if c.get("verdict") else "") + prose.one_line(c.get("text") or "")
             for c in sorted(own, key=lambda c: -_rank(c))]
    if inside:
        n = Counter(c.get("verdict") or "" for c in inside)
        line = f"Comments {where}:"
        for v in sorted((v for v in n if v), key=lambda v: -comments_lib.VERDICT_RANK.get(v, -1)):
            line += " " + prose.mark(v) + " " + prose.code(n[v])
        if n[""]:
            line += ("," if len(n) > 1 else "") + " " + prose.code(n[""]) + " without a verdict"
        lines.insert(0, line)
    return lines


def describe_node(nd: dict | None, own: list[dict], inside: list[dict] = (), kind: str = "") -> str:
    """Write what a change did to a drawn node and the comments that show on it. ``nd`` is the
    node's ``change`` value, ``own`` the comments about it and ``inside`` those about what it
    holds, which are counted when there are several. The count names the node by ``kind``, "in
    this directory"; it calls a module's own code, or a node of no kind, "this box", as the
    reader sees it."""
    lines: list[str] = []
    if nd:
        t, mine = nd["totals"], nd.get("own") or {}
        added, removed = (t["added"], t["removed"]) if t["added"] or t["removed"] else (mine.get("added", 0), mine.get("removed", 0))
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
        if mine.get("old_path"):
            lines.append(prose.say("Renamed from {}", mine["old_path"]))
        if mine.get("renamed_to"):
            lines.append(prose.say("Renamed to {}", mine["renamed_to"]))
    where = f"in this {kind}" if kind and kind != "anchor" else "in this box"
    return prose.paragraphs(lines + _list_comment_lines(own, inside, where))


def describe_edge(ed: dict | None, own: list[dict], inside: list[dict] = (), names: dict[str, str] | None = None) -> str:
    """Write how a change altered a drawn edge's references, which names came or went, and the
    comments that show on it. ``ed`` is the edge's ``change`` value, ``names`` maps the targets of
    its references to their names, ``own`` holds the comments about the edge and ``inside`` those
    about the edges it bundles."""
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
    return prose.paragraphs(lines + _list_comment_lines(own, inside, "on this edge"))
