# Rewrite: model / overlay / scene (working plan, not for merging)

Agreed with the owner 2026-11-03 (sketch: `sketch/`). Breaking user-visible behaviour, the
JSON the viewer reads and stored data is fine (no users): simplicity of the design first.

## Core

- `nolli/model/`: `Model` is structure only: `kinds` (address -> node kind), `edges`
  (`EdgeKey(kind, src, dst, subkind)`, `contain` among the kinds), `refs` (role -> key ->
  keys in that source), `meta`, `key`. Metamodel checks on build. `FORMAT` in model.json;
  loading refuses another.
- `nolli/scene.py`: `Element` (key, kind, model_key, sources, src/dst for an edge, get,
  out_edges/in_edges/children/parent for structure), `Overlay` (`name`, `compute(element)`),
  `Scene` (model, overlays, sources; elements, cache, cycle check; `memo(fn)` for
  whole-scene values an overlay needs, e.g. the numbered changes).
- Storage: `MapStore.open_maps(name) -> StoredMaps` (`load(model_key)`, `save(model_key,
  values, about)`, `load_about`). Extractors save their maps when a model is built; review
  flows save verdicts.

## Extraction

- The old mutable `Model` becomes the extraction builder `nolli/extractors/facts.py`
  (`Facts`, `Entity`, `Relation`, `Provenance`), so extractors keep their code. Attribute
  writes are recorded per extractor (the running one), so `Facts.split()` gives the
  structure `Model` and one map per extractor (node attrs; edge facts as a list).
- overlays per extractor: `fs`, `python-imports`, … (stored); `facts` merges them for code
  that does not care which extractor said it (hover text, roles, labels).

## Transformations

- `merge(base, head)` -> change scene: union, refs `base`/`head`. The `content` overlay
  (git's hunks per file, saved when the change is built, from `sources`) is the only thing
  from git; merge itself reads no git.
- `project(scene, state)` -> projected scene: today's cut + roll-up + lift + utilities +
  presets (`transform/`), drawn nodes keep their address (own code `x#` is a node of the
  projected model), refs `source` -> what each stands for. What the projection decided
  (container, chips, badge, utility, ends, counts, via, …) is its own overlay
  (`projection`), as an extractor's facts are.

## Overlays on top

`label` (name, icons), `hover` (info.py), `change` (status, totals, facts that changed;
also the numbered changes and details as queries), `verdicts` (stored by address: a node,
or an edge `a --> b`; rolled up through refs), `review` (items, the review card). The
viewer's scene JSON is generic: each element's overlays flattened into it (no name clashes
by construction), so a new overlay shows up without exporter code.

## Store

Directory per model key: `key.json`, `meta.json`, `model.json` (commit) and
`overlays/<name>.json`; a change: `base/` and `head/` (commit datasets) plus its own
`overlays/` (content, verdicts). Self-contained, so a CI artifact is one directory.

## Order

1. baseline snapshots of the server's answers (scratchpad golden/)
2. model + scene + facts split + store + build
3. merge + project + overlays
4. exports, server, cli, ci, review agent
5. JS where the JSON changed; tests; e2e
6. compare with baseline, explain every difference; docs; remove the old modules
