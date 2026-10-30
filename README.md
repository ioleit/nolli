# Nolli

In 1748, Giambattista Nolli published the *Pianta Grande di Roma*, the most accurate
map of Rome ever made, produced not from plans or intentions but from twelve years of
surveying the city as it actually stood. Nolli does the same for a codebase: it derives
the architecture from what is really there — imports, symbol references, service
definitions, API contracts — not from what docs or specs claim.

**Status: prototype.** Everything here is a first version that proved an idea on a
handful of repositories. Nothing is a settled design; see "State of each part" below
before trusting any of it.

## What it does

`nolli extract <repo>` reads a repository and writes a model: entities (directories,
modules, classes, services, images, endpoints, environment variables, external
packages) and typed relations between them (`contain`, `use`, `provide`, `allocate`),
each with the extractor, file and line it came from and a confidence class. Facts are
stored at the finest level that could be resolved (class → class, module → endpoint).

The viewer rolls those facts up to boxes at an adjustable depth, so one picture can show
top-level components and, a click later, the classes inside one module. Deterministic:
the same commit gives the same model and the same default picture.

Extracted today: Python (modules, imports, classes and their references, FastAPI/Flask
routes, `os.environ` reads), TypeScript/JavaScript (modules, imports, `fetch`/axios/
EventSource calls, `process.env`/`import.meta.env` reads), docker-compose services and
Dockerfiles. Frontend calls are bound to backend routes by path shape, so a mixed repo
shows the HTTP API as the seam between the two.

## Usage

```
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/nolli extract <repo> -o <out_dir>
python -I viewer/serve.py --data <parent of out_dirs> --host 127.0.0.1 --port 8720
```

`extract` writes `model.json` (facts), `view.json` (the default cut), `scene.json`
(what the viewer draws for that cut), `default.mmd` (Mermaid of it) and `report.json`
(counts, residue, extractor notes). The viewer lists every out_dir under `--data`.

`/bench.html` on the same server measures how well layouts keep the reader's mental map over single unfold/fold steps (`viewer/js/stability.js`: kept left/right and above/below relations between boxes, kept nearest neighbours, movement left after the best shift and scale).

Unfolding and folding animate (`viewer/js/transition.js` plans, `animate.js` plays): new boxes open out of the box they belong to, leaving ones shrink into it, the rest slide, and the view moves with the clicked box so it stays where it was on screen. "animate unfold and fold" in the options (or `an=0`) turns the motion off; the clicked box still stays in place.

The viewer computes nothing itself: on every click it asks the server for the scene
(`/api/scene?d=<dataset>&x=<unfolded ids>&…`, the same keys as its location hash) and
lays it out with ELK. "raw JSON" in its options shows the response instead of a drawing.

## State of each part

Honest marks on how much thought each part has had. Treat everything as open.

| part | state |
|---|---|
| `nolli/model.py` | small and unchanged through many iterations, but a convenience layer, not a settled schema; the entity/relation vocabulary follows UML 2 component and deployment diagrams because they were there, not because it was derived |
| `nolli/extractors/fs.py` | file tree plus a role per file (source/test/config/docs/…); the role rules are guesses that fit a few repos |
| `nolli/extractors/python/`, `typescript/`, `infra/` | first stab at a reasonable split: a language front-end (imports, symbols) next to the adapters that read that language's syntax for a framework; each adapter is a heuristic over one library's idioms |
| `nolli/extractors/interfaces.py` | barely exists; where languages are supposed to meet (how an endpoint or env variable is named) |
| `nolli/match.py` | resolution of placeholders to files, classes and endpoints; grew by patching cases the test repos broke |
| `nolli/rollup.py`, `nolli/rollup.md` | the roll-up from facts to a picture and the rules tuned on top of it; the central idea, but every rule is a judgement call from one afternoon — see `NOTES.md` |
| `nolli/view.py` | one greedy heuristic for the default cut, with magic numbers |
| `nolli/scene.py` | a view state in, everything the viewer draws out (nodes with their drawn parent, edges, labels, badges, emphasis, tooltips); ids are stable across states so two scenes can be diffed |
| `nolli/mermaid.py` | static export of the default cut so a README or PR can show it |
| `viewer/` | temporary throwaway visualization: `index.html` (markup), `viewer.css`, and ES modules in `js/` loaded without a build step (`layout/` scene → positions and edge routes, no DOM, one module per engine: ELK.js layered (one layout over all levels, or each box separately), or Cytoscape.js fCoSE force-directed, chosen in the sidebar; `draw.js` → SVG; `main.js` wiring; state, sidebar, raw JSON, pan/zoom); orthogonal edges that look like a circuit board; redraws from scratch on every change |
| `viewer/package.json`, `tsconfig.json`, `types/`, `test/` | Node is only for development: `npm install`, then `npm run check` (TypeScript checks the JavaScript through its JSDoc types, strict) and `npm test` (unit tests of the pure modules). The browser loads the `.js` files as they are |
| `viewer/serve.py` | tiny server for local use: static files, datasets, and `/api/scene` (standard library only; imports `nolli` from the checkout it sits in) |
| `tests/test_smoke.py` | one synthetic mixed repo pinning the seams that broke once; nothing tests the roll-up rules |
| `dev/` | local workflow: extract a set of repositories you keep outside this repo (`corpus.example.toml`) |

`NOTES.md` records the judgement calls made so far and why, all provisional.
`BACKLOG.md` is what to do next.
