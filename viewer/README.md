# Viewer

`serve.py` is a small local server (standard library only): static files, the datasets
under `--data`, and `/api/scene`. This directory is installed as the package `nolli.viewer`
(see `pyproject.toml`), and `nolli view` starts the server; run as a script from a clone
(`python viewer/serve.py --data …`), it imports `nolli` from that clone.

The viewer computes nothing itself: on every click it asks the server for the scene
(`/api/scene?d=<dataset>&x=<unfolded ids>&…`, the same keys as its location hash, so a
URL is a view) and lays it out with ELK. Tabs above the canvas switch between the
drawing ("Diagram"), the response itself ("JSON") and the static Mermaid export of the
default cut ("Mermaid").

## Files

- `index.html` (markup), `viewer.css` (every colour a variable) and `themes.css`
  (themes that override them, chosen under Layout).
- `js/`: ES modules loaded without a build step. `layout/` turns a scene into
  positions and edge routes, with no DOM, one module per engine: ELK.js layered (one
  layout over all levels, or each box separately) or Cytoscape.js fCoSE
  force-directed, chosen in the sidebar. `draw.js` renders SVG, `main.js` wires it
  together; the rest are state, sidebar, JSON and Mermaid views, pan/zoom, and the
  review column (`review.js`).

## Animation

Unfolding and folding animate (`js/transition.js` plans, `js/animate.js` plays): new
boxes open out of the box they belong to, leaving ones shrink into it, the rest slide,
and the view moves with the clicked box so it stays where it was on screen. "Animate"
under Layout (or `an=0` in the hash) turns the motion off; the clicked box still stays
in place.

## Layout stability bench

`/bench.html` on the same server measures how well layouts keep the reader's mental map
over single unfold/fold steps (`js/stability.js`): kept left/right and above/below
relations between boxes, kept nearest neighbours, and movement left after the best
shift and scale.

## Development

Node is only for development: `npm install`, then `npm run check` (TypeScript checks
the JavaScript through its JSDoc types, strict) and `npm test` (unit tests of the pure
modules). The browser loads the `.js` files as they are.

The libraries it uses (ELK.js, Cytoscape.js with fCoSE, Mermaid) are in `vendor/`, so the
viewer works offline and from an installed package. `npm run vendor` copies them there
from `node_modules` (`vendor.mjs`; versions in `package.json`, licences in
`vendor/LICENSES.txt`); run it after changing a version, and commit the result.
