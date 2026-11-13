"""Says what a change did to each node and edge, as the ``change`` overlay."""

from __future__ import annotations

from collections.abc import Mapping

from nolli.logic.transformations import merge
from nolli.logic.transformations.merge import Links
from nolli.model import schema, vocabulary
from nolli.model.model import Key, Relation
from nolli.model.scene import Element, Overlay, Scene, StoredMaps
from nolli.model.schema import EntityId, RelationId

TOTALS = ("added", "removed", "files", "new", "gone")  # what a node of a change counts, and a drawn node sums up
OWN = ("status", "git", "added", "removed", "old_path", "renamed_to")  # what a drawn node's ``own`` keeps of its entity's value
MAX_HUNKS = 30  # a drawn file lists at most this many hunks
MAX_CHANGES = 40  # a drawn edge lists at most this many changed edges


class _Side:
    """One side of a change, with the maps the comparison needs read all at once."""

    def __init__(self, scene: Scene) -> None:
        self.model = scene.model
        self.references = vocabulary.read_all_references(scene)
        self.symbols = vocabulary.read_all_symbols(scene)


class Change(Overlay):
    """The overlay that says what a change did to each node and edge.

    A commit has no values. A change has values for the nodes and edges that changed, found by
    comparing its two sides; what git says changed inside each file comes from the change's
    ``content`` map (``logic/sources/git.py``). A scene drawn from a change has values for the
    nodes and edges that stand for something that changed, summed up from the change's values.
    Nodes and edges that did not change have no value.

    On a node of a change:

    - ``status``: ``added`` or ``removed`` if only one side has the node, else ``same``.
    - ``git``: git's status letter for a file it changed (``A``, ``M``, ``D``, ``R``).
    - ``added``, ``removed``: the number of lines git added and removed, in a file, or inside a
      class's line range.
    - ``hunks``: a file's hunks, each ``{new, old, added, removed}``: the line ranges on each
      side and the line numbers added and removed.
    - ``added_lines``, ``removed_lines``: a class's changed line numbers.
    - ``old_path``: a renamed file's path before. ``renamed_to``: on the file at the old path,
      the path after.
    - ``totals``: what the node adds to the sums of the nodes drawn around it, one number for
      each of ``TOTALS``: lines ``added`` and ``removed`` and ``files`` changed (counted on files
      only, so lines are not counted twice), and nodes ``new`` and ``gone``.

    On an edge of a change:

    - ``status``: ``added``, ``removed``, or ``changed`` if its number of references differs.
    - ``base``, ``head``: its number of references on each side.
    - ``fact``: the edge described for a reader (``FactChange`` in nolli.d.ts), with a link to
      the first place it changed.

    On a node of a drawn scene (``NodeDiff`` in nolli.d.ts):

    - ``status``: ``added`` or ``removed`` if the entity it is was added or removed, else
      ``changed``.
    - ``totals``: the sums of ``totals`` over what the node stands for and the nodes drawn
      inside it. A class's own lines are not in them (its file counts them); they are in ``own``.
    - ``internal``: the number of changed edges the projection found between the nodes it holds.
    - ``role``: the role of the file or directory it is in; ``source`` for ``mixed`` and for a
      node outside the file tree.
    - ``own``: only if the entity the node is changed itself, what the change did to it: its
      ``status``, ``git``, ``added`` and ``removed`` lines, ``old_path`` and ``renamed_to`` as on
      the change. For a file or class also a ``link`` to where its change shows on GitHub. For a
      file also its ``hunks``, each with a ``link`` (at most ``MAX_HUNKS``; ``more_hunks`` counts
      the rest), and the ``classes`` in it the change touched, each
      ``{name, status, added, removed}``. A node without ``own`` changed only inside.

    On an edge of a drawn scene (``EdgeDiff`` in nolli.d.ts):

    - ``status``, ``base``, ``head``: as the projection counted the references it stands for.
    - ``facts``: the ids of the changed edges of the change it stands for.
    - ``changes``: the first ``MAX_CHANGES`` of those edges' ``fact``; ``more`` counts the rest.
    - ``role``: the role of its source node.
    """

    name = "change"

    def __init__(self, content: StoredMaps) -> None:
        self.content = content

    def compute(self, el: Element) -> dict | None:
        if merge.is_change(el.scene):  # computed for the whole change in compute_all
            return el.scene.read_all(self.name).get(el.key)
        if "source" in el.scene.sources:  # a drawn scene sums up the changes of what each element stands for
            return self._sum_edge(el) if el.is_edge else self._sum_node(el)
        return None

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """Compare each node and edge of a change between its two sides. Sum up a scene drawn
        from a change element by element. Return nothing for a commit."""
        if not merge.is_change(scene):
            return Overlay.compute_all(self, scene) if "change" in scene.model.meta else {}
        out: dict[Key, dict] = {}
        sides = {role: _Side(scene.sources[role]) for role in ("base", "head")}
        content = self.content.load(scene.model.key.format_key())
        renames = {EntityId.from_path(v["old_path"]): k.path for k, v in content.items() if not isinstance(k, RelationId) and v.get("old_path")}
        by_old_path = {v["old_path"]: v for k, v in content.items() if not isinstance(k, RelationId) and v.get("old_path")}
        for x in scene.model.entities.values():
            rec = self._compare_node(x.id, x.kind, sides, content, renames, by_old_path)
            if rec is not None:
                out[x.id] = rec
        meta = scene.model.meta
        for e in scene.model.edges:
            rec = self._compare_edge(e, meta, {role: s.model.has(e.id) and s.references.get(e.id, []) for role, s in sides.items()})
            if rec is not None:
                out[e.id] = rec
        return out

    # ---------- on the change ----------

    def _compare_node(self, x: str, kind: str, sides: Mapping[str, _Side], content: Mapping[Key, dict],
                      renames: Mapping[str, str], by_old_path: Mapping[str, dict]) -> dict | None:
        """Compare a node between the two sides, and return what the change did to it, or None
        if it did nothing. ``renames`` maps the old address of each renamed file to its new
        path, and ``by_old_path`` maps the old path to git's content of the file."""
        b, h = sides["base"].model.has(x), sides["head"].model.has(x)
        rec: dict = {}
        if not b:
            rec["status"] = "added"
        elif not h:
            rec["status"] = "removed"
        c = content.get(x)
        if c is not None and kind == "file":
            rec.setdefault("status", "same")
            rec.update(c)
        if kind == "file" and rec.get("status") == "removed":
            gone = renames.get(x)
            if gone:
                rec["renamed_to"] = gone
        if kind in schema.SYMBOL_KINDS:
            lines = self._list_class_lines(x, sides["base"].symbols.get(x, {}) if b else {},
                                           sides["head"].symbols.get(x, {}) if h else {}, content, by_old_path)
            if lines:
                rec.setdefault("status", "same")
                rec.update(lines)
        if not rec:
            return None
        files = kind == "file" and "git" in rec  # lines are counted by files, so a class's are not counted twice
        rec["totals"] = {"added": rec.get("added", 0) if files else 0, "removed": rec.get("removed", 0) if files else 0,
                         "files": int(files), "new": int(rec["status"] == "added" and kind != "group"),
                         "gone": int(rec["status"] == "removed" and kind != "group")}
        return rec

    @staticmethod
    def _list_class_lines(x: str, before: Mapping, after: Mapping, content: Mapping[Key, dict],
                          by_old_path: Mapping[str, dict]) -> dict | None:
        """Find the lines git changed inside a class's line range on each side, and return their
        counts and numbers, or None if there are none. ``before`` and ``after`` are what the
        class's language says about it on each side."""
        f = x.file
        c = content.get(f)
        if c is None:  # a class of a renamed file, by its old path
            c = by_old_path.get(f.path)
        if c is None:
            return None
        old_path = c.get("old_path") or f.path
        added = [n for hk in c.get("hunks", []) for n in hk["added"]
                 if after and after.get("line", 0) <= n <= (after.get("end_line") or 0)]
        removed = [n for hk in c.get("hunks", []) for n in hk["removed"]
                   if before and before.get("path") == old_path and before.get("line", 0) <= n <= (before.get("end_line") or 0)]
        if not (added or removed):
            return None
        return {"added": len(added), "removed": len(removed), "added_lines": added, "removed_lines": removed}

    @staticmethod
    def _compare_edge(e: Relation, meta: dict, refs: Mapping[str, list | bool]) -> dict | None:
        """Compare an edge's references between the two sides, and return what the change did to
        it, or None if their number is the same. ``refs`` maps each side to the edge's
        references there, or to False if that side has no such edge."""
        b, h = refs["base"], refs["head"]
        nb = sum(r["count"] for r in b) if b is not False else 0
        nh = sum(r["count"] for r in h) if h is not False else 0
        if b is False:
            rec = {"status": "added", "base": 0, "head": nh}
        elif h is False:
            rec = {"status": "removed", "base": nb, "head": 0}
        elif nb != nh:
            rec = {"status": "changed", "base": nb, "head": nh}
        else:
            return None
        said = b if rec["status"] == "removed" else h
        first = said[0] if said else {}
        path, line = first.get("path"), first.get("line")
        link = Links(meta.get("change") or {}).diff(path, line, "base" if rec["status"] == "removed" else "head") if path else None
        return rec | {"fact": {"fact": e.id, "status": rec["status"], "kind": e.kind, "subkind": e.subkind, "src": e.src, "dst": e.dst,
                               "base": rec["base"], "head": rec["head"], "path": path, "line": line, "link": link}}

    # ---------- on a scene drawn from the change ----------

    def _sum_node(self, el: Element) -> dict | None:
        """Sum up what changed in what a drawn node stands for and in the nodes drawn inside it,
        and return None if nothing did."""
        if el.kind == "anchor":
            return None
        totals = dict.fromkeys(TOTALS, 0)
        own, seen = None, False
        for s in el.sources.get("source", ()):
            rec = s.get(self.name)
            if rec:
                seen = True
                own = rec if s.key == el.key else own
                for k in TOTALS:
                    totals[k] += (rec.get("totals") or {}).get(k, 0)
        for c in el.children:
            rec = c.get(self.name)
            if rec:
                seen = True
                for k in TOTALS:
                    totals[k] += (rec.get("totals") or {}).get(k, 0)
        internal = (el.get("projection") or {}).get("internal_changes", 0)
        status = own["status"] if own and own["status"] in ("added", "removed") else "changed" if seen or internal else None
        if status is None:
            return None
        out = {"status": status, "totals": totals, "internal": internal}
        same = el.find_same("source")
        if same is not None:
            out["role"] = self._find_role(same)
        if own:
            out["own"] = self._describe_own(same, own) if same is not None else {k: own[k] for k in OWN if k in own}
        return out

    def _describe_own(self, el: Element, own: dict) -> dict:
        """Describe what the change did to an entity itself, from ``own``, its value on the change:
        its ``OWN`` fields, and for a file or class a link to where its change shows on GitHub.
        For a file, also list its hunks, each linked, and the classes in it the change touched."""
        out = {k: own[k] for k in OWN if k in own}
        if not el.key.is_repo or el.kind not in ("file", *schema.SYMBOL_KINDS):
            return out
        links = Links(el.scene.model.meta.get("change") or {})
        path, st = el.key.path, own.get("status")
        if el.kind != "file":
            a = el.get(vocabulary.SYMBOLS) or {}
            return out | {"link": links.blob(path, "base" if st == "removed" else "head", a.get("line"), a.get("end_line"))
                          if st in ("added", "removed") else links.diff(path)}
        hunks = [{"new": h["new"], "old": h["old"], "added": len(h["added"]), "removed": len(h["removed"]),
                  "link": links.diff(path, (h["added"] or [None])[0] or (h["removed"] or [None])[0], "head" if h["added"] else "base")}
                 for h in own.get("hunks", [])]
        classes = [{"name": (c.get("names") or {}).get("name", c.key), "status": rec["status"], "added": rec.get("added", 0),
                    "removed": rec.get("removed", 0)}
                   for c in el.children if c.kind in schema.SYMBOL_KINDS and (rec := c.get(self.name))]
        return out | {"link": links.diff(path), "hunks": hunks[:MAX_HUNKS], "more_hunks": max(0, len(hunks) - MAX_HUNKS),
                      "classes": classes}

    @staticmethod
    def _find_role(el: Element) -> str:
        """Return the role of the file or directory a node of the change is in. Return ``source``
        for ``mixed`` and for a node outside the file tree (a service, an endpoint, a package)."""
        cur: Element | None = el
        while cur is not None:
            role = (cur.get(vocabulary.FILES) or {}).get("role")
            if role and cur.kind in ("file", "directory"):
                return "source" if role == "mixed" else role
            cur = cur.parent
        return "source"

    def _sum_edge(self, el: Element) -> dict | None:
        """Sum up how the references a drawn edge stands for changed, as the projection counted
        them, and list the changed edges among them. Return None if it did not change."""
        p = el.get("projection") or {}
        if not p.get("status"):
            return None
        changes: list[dict] = []
        for s in el.sources.get("source", ()):
            rec = s.get(self.name)
            if rec:
                changes.extend(rec["changes"] if "changes" in rec else [rec["fact"]])
        changes.sort(key=lambda c: c["fact"])
        src = el.src.find_same("source")
        return {"status": p["status"], "base": p.get("base", 0), "head": p.get("head", 0), "facts": [c["fact"] for c in changes],
                "changes": changes[:MAX_CHANGES], "more": max(0, len(changes) - MAX_CHANGES),
                "role": self._find_role(src) if src is not None else "source"}
