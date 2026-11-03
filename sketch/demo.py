"""Try the sketch on nolli's own models: python3 -I sketch/demo.py <base model.json> <head model.json>"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core import CONTAIN, EdgeKey, Element, Key, Model, Scene, export_json, merge, project  # noqa: E402


class DictStore:
    """A MapStore in memory: maps by (name, model key)."""

    def __init__(self) -> None:
        self.maps: dict[tuple[str, str], dict] = {}

    def open_maps(self, name: str) -> "DictMaps":
        return DictMaps(self, name)


class DictMaps:
    def __init__(self, store: DictStore, name: str) -> None:
        self.store, self.name = store, name

    def load(self, model_key):
        return self.store.maps.get((self.name, model_key), {})

    def save(self, model_key, values):
        self.store.maps[(self.name, model_key)] = dict(values)


def name_extractor(provenance: dict | None) -> str:
    """Today's extractor names (fs, python-symbols, typescript-fetch, …) by family."""
    return (provenance or {}).get("extractor", "fs").split("-")[0]


def load_extracted(path: Path, key: str, store: DictStore) -> tuple[Model, set[str]]:
    """Today's model.json split into structure (the Model) and what each extractor said
    about each element (one stored map per extractor)."""
    d = json.loads(path.read_text())
    kinds = {e["id"]: e["kind"] for e in d["entities"]}
    maps: dict[str, dict[Key, dict]] = {}
    for e in d["entities"]:
        maps.setdefault(name_extractor(e.get("provenance")), {})[e["id"]] = {"name": e["name"], **e["attrs"]}
    edges = set()
    for r in d["relations"]:
        k = EdgeKey(r["src"], r["dst"], r["kind"])
        edges.add(k)
        if r["kind"] != CONTAIN:
            v = maps.setdefault(name_extractor(r.get("provenance")), {}).setdefault(k, {"count": 0, "names": []})
            v["count"] += r.get("count", 1)
            v["names"].append(r["attrs"].get("name") or r["dst"])
    for name, values in maps.items():
        store.open_maps(name).save(key, values)
    return Model(key, kinds, edges), set(maps)


# --- Overlays ---------------------------------------------------------------------------


class ExtractorOverlay:
    """What one extractor said. A drawn node is what it is; a lifted edge sums the
    references it stands for; in a change, head's word if it has one, else base's."""

    def __init__(self, name: str, stored) -> None:
        self.name, self.stored = name, stored

    def compute(self, el: Element) -> dict | None:
        if (own := self.stored.load(el.model_key).get(el.key)) is not None:
            return own
        same = [v for role in ("head", "base", "source") for s in el.sources.get(role, ())
                if s.key == el.key and (v := s.get(self.name)) is not None]
        if same:
            return same[0]
        if el.is_edge:
            parts = [v for s in el.sources.get("source", ()) if (v := s.get(self.name))]
            if parts:
                return {"count": sum(p["count"] for p in parts), "names": sorted({n for p in parts for n in p["names"]})}
        return None


RANK = {"problem": 3, "question": 2, "good": 1, "fine": 0}


class Verdicts:
    name = "verdicts"

    def __init__(self, stored) -> None:
        self.stored = stored

    def compute(self, el: Element) -> dict | None:
        own = self.stored.load(el.model_key).get(el.key)
        under = [v for s in el.sources.get("source", ()) if (v := s.get(self.name))]
        found = ([own] if own else []) + under
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        worst = max(found, key=lambda v: RANK[v["verdict"]])
        return {"verdict": worst["verdict"], "count": sum(v.get("count", 1) for v in found)}


class FoldedKinds:
    """The feature request: what kinds of things a folded box stands for."""

    name = "folded_kinds"

    def compute(self, el: Element) -> dict | None:
        if el.is_edge:
            return None
        kinds = Counter(s.kind for s in el.sources.get("source", ()) if s.key != el.key)
        return dict(kinds.most_common()) or None


class Change:
    """In a merge: added / removed / changed / unchanged, by comparing what the extractors
    said on both sides. Folded: what changed under a drawn node or edge."""

    name = "change"
    FINGERPRINT = {"fs": ("size",), "python": ("line", "end_line"), "typescript": ("line", "end_line")}

    def compute(self, el: Element) -> dict | None:
        if "base" in el.sources:
            base, head = el.sources["base"], el.sources["head"]
            if not base:
                return {"status": "added"}
            if not head:
                return {"status": "removed"}
            b, h = base[0], head[0]
            if el.is_edge:
                differs = any((b.get(n) or {}).get("count") != (h.get(n) or {}).get("count") for n in self.FINGERPRINT)
            else:
                differs = any([(b.get(n) or {}).get(f) for f in fs] != [(h.get(n) or {}).get(f) for f in fs]
                              for n, fs in self.FINGERPRINT.items())
            return {"status": "changed" if differs else "unchanged"}
        statuses = Counter((s.get(self.name) or {}).get("status") for s in el.sources.get("source", ()))
        statuses.pop("unchanged", None)
        statuses.pop(None, None)
        return dict(statuses) or None


HIDDEN_ROLES = {"test", "docs", "config", "data", "asset"}


def is_source(el: Element) -> bool:
    return (el.get("fs") or {}).get("role") not in HIDDEN_ROLES


def main(base_path: str, head_path: str) -> None:
    store = DictStore()
    base_model, names_b = load_extracted(Path(base_path), "nolli@0.6.0", store)
    head_model, names_h = load_extracted(Path(head_path), "nolli@0.7.0", store)
    # verdicts as some other flow would have saved them for the head commit
    store.open_maps("verdicts").save("nolli@0.7.0", {
        "repo:nolli/extractors/__init__.py": {"verdict": "problem", "reason": "registry and Context in one module"},
        "repo:nolli/extractors/api.py": {"verdict": "question", "reason": "resolve lives with extraction"},
        "repo:nolli/scene.py": {"verdict": "problem", "reason": "projection imports review"},
    })
    overlays = [*(ExtractorOverlay(n, store.open_maps(n)) for n in sorted(names_b | names_h)),
                Verdicts(store.open_maps("verdicts")), FoldedKinds(), Change()]
    print("overlays:", ", ".join(o.name for o in overlays))
    base, head = Scene(base_model, overlays), Scene(head_model, overlays)

    folded = project(head, {"repo:", "repo:nolli"}, keep=is_source)
    print("\n== 0.7.0, nolli/ unfolded one level, source only ==")
    for a in ("repo:nolli/extractors", "repo:nolli/scene.py"):
        el = folded.find_element(a)
        print(a, el.kind, {n: el.get(n) for n in ("verdicts", "folded_kinds")}, "fs role:", el.get("fs")["role"])
    e = folded.find_element(EdgeKey("repo:nolli/build.py", "repo:nolli/extractors", "use"))
    print(e.key, e.get("python"))

    change = merge(base, head)
    shown = project(change, {"repo:", "repo:nolli"}, keep=is_source)
    print("\n== change 0.6.0 -> 0.7.0, same fold ==")
    for el in shown.list_elements():
        if not el.is_edge and (c := el.get("change")):
            print(el.key, c)
    print("\nexported:", len(json.dumps(export_json(shown))), "bytes of JSON,",
          len(shown.model.kinds), "nodes,", sum(e.kind != CONTAIN for e in shown.model.edges), "edges")


if __name__ == "__main__":
    main(*sys.argv[1:3])
