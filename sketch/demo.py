"""Try the sketch on nolli's own models: python3 -I sketch/demo.py <base model.json> <head model.json>"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core import EdgeKey, Element, Model, Scene, StoredOverlay, export_json, merge, project  # noqa: E402

STRUCTURE = {"use", "provide", "allocate"}


class DictStore:
    def __init__(self) -> None:
        self.maps: dict[tuple[str, str], dict] = {}

    def load_map(self, model_key, overlay):
        return self.maps.get((model_key, overlay))

    def save_map(self, model_key, overlay, values):
        self.maps[(model_key, overlay)] = dict(values)


def load_extracted(path: Path, key: str, store: DictStore) -> Model:
    """Today's model.json split into structure (the Model) and what the extractors said
    about each element (the stored map of the "extracted" overlay)."""
    d = json.loads(path.read_text())
    parent = {e["id"]: None for e in d["entities"]}
    values: dict[Element, dict] = {e["id"]: {"kind": e["kind"], "name": e["name"], **e["attrs"]} for e in d["entities"]}
    edges = set()
    for r in d["relations"]:
        if r["kind"] == "contain":
            parent[r["dst"]] = r["src"]
        elif r["kind"] in STRUCTURE:
            e = EdgeKey(r["src"], r["dst"], r["kind"])
            edges.add(e)
            v = values.setdefault(e, {"count": 0, "names": []})
            v["count"] += r.get("count", 1)
            v["names"].append(r["attrs"].get("name") or r["dst"])
    store.save_map(key, "extracted", values)
    return Model(key, parent, edges)


# --- Overlays ---------------------------------------------------------------------------


class Extracted(StoredOverlay):
    """A drawn node is what it is; a lifted edge sums the references it stands for."""

    def combine(self, element, parts, scene):
        own = [v for _, e, v in parts if e == element]
        if own:
            return own[-1]  # merge: head's if present, else base's
        if isinstance(element, EdgeKey):
            return {"count": sum(v.get("count", 0) for _, _, v in parts),
                    "names": sorted({n for _, _, v in parts for n in v.get("names", [])})}
        return None


RANK = {"problem": 3, "question": 2, "good": 1, "fine": 0}


class Verdicts(StoredOverlay):
    def combine(self, element, parts, scene):
        own = [v for _, e, v in parts if e == element]
        under = [v for _, e, v in parts if e != element]
        verdicts = own + under
        worst = max(verdicts, key=lambda v: RANK[v["verdict"]])
        return {"verdict": worst["verdict"], "count": len(verdicts), "reason": own[0]["reason"] if own else None}


class FoldedKinds:
    """The feature request: what kinds of things a folded box stands for."""

    name = "folded_kinds"

    def compute(self, element, scene):
        if isinstance(element, EdgeKey) or "source" not in scene.sources:
            return None
        src = scene.sources["source"]
        under = [e for e in scene.list_source_elements(element) if e != element]
        kinds = Counter((src.get(e, "extracted") or {}).get("kind") for e in under)
        return dict(kinds.most_common()) or None


class Change:
    """On a merged scene: added / removed / changed / unchanged. On a projection of one:
    what changed under each drawn node or edge."""

    name = "change"

    def compute(self, element, scene):
        if "base" in scene.sources:
            b = scene.sources["base"].get(element, "extracted") if scene.sources["base"].model.has(element) else None
            h = scene.sources["head"].get(element, "extracted") if scene.sources["head"].model.has(element) else None
            if b is None:
                return {"status": "added"}
            if h is None:
                return {"status": "removed"}
            fields = ("lines", "count", "end_line")
            return {"status": "changed" if any(b.get(f) != h.get(f) for f in fields) else "unchanged"}
        if "source" in scene.sources:
            src = scene.sources["source"]
            statuses = Counter((src.get(e, "change") or {}).get("status") for e in scene.list_source_elements(element))
            statuses.pop("unchanged", None)
            statuses.pop(None, None)
            return dict(statuses) or None
        return None


HIDDEN_ROLES = {"test", "docs", "config", "data", "asset"}


def is_source(scene: Scene, a: str) -> bool:
    return (scene.get(a, "extracted") or {}).get("role") not in HIDDEN_ROLES


def main(base_path: str, head_path: str) -> None:
    store = DictStore()
    overlays = [Extracted("extracted"), Verdicts("verdicts"), FoldedKinds(), Change()]
    base = Scene(load_extracted(Path(base_path), "nolli@0.6.0", store), overlays, store=store)
    head = Scene(load_extracted(Path(head_path), "nolli@0.7.0", store), overlays, store=store)
    # verdicts as some other flow would have saved them for the head commit
    store.save_map("nolli@0.7.0", "verdicts", {
        "repo:nolli/extractors/__init__.py": {"verdict": "problem", "reason": "registry and Context in one module"},
        "repo:nolli/extractors/api.py": {"verdict": "question", "reason": "resolve lives with extraction"},
        "repo:nolli/scene.py": {"verdict": "problem", "reason": "projection imports review"},
    })

    folded = project(head, {"repo:", "repo:nolli"}, keep=is_source)
    print("== 0.7.0, nolli/ unfolded one level, source only ==")
    for n in ("repo:nolli/extractors", "repo:nolli/scene.py"):
        print(n, folded.list_properties(n))
    e = next(e for e in folded.model.edges if e.src == "repo:nolli/build.py" and e.dst == "repo:nolli/extractors")
    print(e, folded.list_properties(e))

    change = merge(base, head)
    shown = project(change, {"repo:", "repo:nolli"}, keep=is_source)
    print("\n== change 0.6.0 -> 0.7.0, same fold ==")
    for a in sorted(shown.model.list_nodes()):
        if (c := shown.get(a, "change")):
            print(a, c)
    print("\nexported:", len(json.dumps(export_json(shown))), "bytes of JSON,",
          len(shown.model.parent), "nodes,", len(shown.model.edges), "edges")


if __name__ == "__main__":
    main(*sys.argv[1:3])
