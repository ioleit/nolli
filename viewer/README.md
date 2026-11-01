# Viewer

`serve.py` is a small local server (standard library only): static files, the datasets
under `--data`, `/api/scene` and `/api/mermaid`. This directory is installed as the package
`nolli.viewer` (see `pyproject.toml`), and `nolli view` starts the server; run as a script
from a clone (`python viewer/serve.py --data …`), it imports `nolli` from that clone.

The viewer computes nothing itself: on every click it asks the server for the scene of a
scene state (`/api/scene?d=<dataset>&x=<unfolded ids>&…`, or `p=<preset>` instead of `x`;
the same keys as its location hash, so a URL is a picture). The tabs above the canvas show
that one scene three ways: "Interactive" lays it out with ELK and draws it, "Mermaid" asks
`/api/mermaid` for the same scene state and lets Mermaid draw it (the text a README, a PR
comment or a review agent gets), and "JSON" shows the response itself. "Fade unchanged"
applies to both drawings. The presets in the sidebar (default, folded, changes, unfolded)
name a cut; clicking a box leaves them ("custom").

## Files

- `index.html` (markup), `viewer.css` (every colour a variable) and `themes.css`
  (themes that override them, chosen under Layout).
- `js/`: ES modules loaded without a build step. `layout/` turns a scene into
  positions and edge routes, with no DOM, one module per engine: ELK.js layered (one
  layout over all levels, or each box separately) or Cytoscape.js fCoSE
  force-directed, chosen in the sidebar. `draw.js` renders SVG, `main.js` wires it
  together; the rest are state, sidebar, the Mermaid and JSON tabs, pan/zoom, and the
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

## Browser tests of the review

`npm run e2e` runs `e2e/` in Chromium with Playwright: it builds a made-up repository
with one reviewed change and the agent's verdicts on it (`tests/fixtures/review_repo.py`,
an invoicing app whose branch adds a cache and overdue alerts and deletes an export),
serves the viewer on it and checks where the verdicts are drawn, what their hover cards
say, and screenshots of each (`e2e/__screenshots__/`, one per thing to look at). ELK is
served from `node_modules` instead of the CDN, so the layout cannot drift under the tests.

The screenshots must match exactly, and fonts differ between systems: make and compare
them on one (they were made on Debian 13 with its Chromium). After a deliberate change to
the look, `npm run e2e:update` remakes them; look at every changed image before
committing it. Python comes from `$NOLLI_PYTHON`, else `../.venv`, else `python3` (with
nolli's dependencies installed); Chromium from `$NOLLI_CHROMIUM`, else `/usr/bin/chromium`,
else Playwright's own (`npx playwright install chromium`).

