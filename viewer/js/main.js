// The viewer only lays out and draws. Which boxes exist, which edges connect them, and
// every label, badge and emphasis flag come from the server: /api/scene (nolli/scene.py).
//
//   state.js    view state, URL hash, scene query
//   layout/     scene -> positions and edge routes, no DOM; one module per engine
//   draw.js     scene + layout -> SVG, with a handle per node to move and fade it
//   transition.js, animate.js: the step between two pictures (plan, then play)
//   raw.js      scene -> JSON view;  mermaid.js: the static export as a view
//   sidebar.js, help.js (explanations on hover), panzoom.js, tip.js, dom.js
//   stability.js, bench.js (bench.html): how well layouts keep the picture across a step
// Types: types/nolli.d.ts, checked with `npm run check`.

import { byId, input, select } from "./dom.js";
import { DEFAULTS, OPTIONS, VIEWS, query, readHash, state, writeHash } from "./state.js";
import { ENGINES, layout } from "./layout/index.js";
import { draw, drawGhosts } from "./draw.js";
import { plan } from "./transition.js";
import { finish, play } from "./animate.js";
import { hideRaw, onRawToggle, showRaw } from "./raw.js";
import { sidebar, syncControls } from "./sidebar.js";
import { fit } from "./panzoom.js";
import { hideMermaid, showMermaid } from "./mermaid.js";
import "./help.js";
import { applyTheme, initialTheme, themeOptions } from "./theme.js";

/** @typedef {import("../types/nolli").Scene} Scene @typedef {import("../types/nolli").Laid} Laid */

// surface failures instead of a silently blank canvas
/** @param {string} msg */
const showError = msg => { const s = byId("status"); s.textContent = "error: " + msg; s.style.color = "#d9534f"; };
window.addEventListener("error", ev => showError(ev.message + (ev.lineno ? ` (${(ev.filename || "").split("/").pop()}:${ev.lineno})` : "")));
window.addEventListener("unhandledrejection", ev => showError(String(ev.reason && ev.reason.message || ev.reason)));

/** @type {Scene | null} the last scene received */
let scene = null;
let seq = 0;
let fitted = false;
/** @type {{ scene: Scene, laid: Laid } | null} the picture on screen */
let shown = null;
/** @type {string | null} the box whose click led to the next picture */
let clicked = null;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

async function refresh(keepZoom = false) {
  const my = ++seq, url = "/api/scene?" + query(), t0 = performance.now();
  const res = await fetch(url);
  const body = await res.json();
  if (my !== seq) return;  // a newer request is under way
  if (!res.ok) { showError(body.error || res.status); return; }
  body.stats.ms.roundtrip = Math.round(performance.now() - t0);
  /** @type {Scene} */
  const sc = body; sc.url = url; scene = sc;
  // adopt the server's normalised state (default cut resolved, folded descendants dropped)
  state.expanded = sc.state.expanded; state.roles = sc.state.roles; state.groups = sc.state.groups; state.budget = sc.state.budget;
  syncControls(); sidebar(sc, () => refresh(true));
  await show(keepZoom);
  writeHash();
}

/** Bring the current view up to date: the diagram, the scene as JSON, or the Mermaid export. */
async function show(keepZoom = false) {
  if (state.view !== "mermaid") hideMermaid();
  if (state.view === "json") showRawPane();
  else if (state.view === "mermaid") { hideRaw(); byId("status").textContent = ""; if (state.name) await showMermaid(state.name); }
  else await render(keepZoom);
}

/** @param {string} id */
function toggle(id) {
  const x = new Set(state.expanded);
  x.has(id) ? x.delete(id) : x.add(id);  // the server drops whatever was unfolded inside a folded box
  state.expanded = [...x]; clicked = id; refresh(true);
}

async function render(keepZoom = false) {
  if (!scene) return;
  hideRaw();
  const sc = scene, prev = shown, from = clicked;
  clicked = null;
  const laid = await layout(sc, { engine: state.engine, direction: state.direction, seed: state.name || "", previous: prev?.laid, toggled: from || undefined });
  if (sc !== scene) return;  // a newer scene arrived while this one was laid out
  finish();
  const root = byId("root");
  const drawn = draw(root, sc, laid, toggle, state.sameWidth);
  shown = { scene: sc, laid };
  if (keepZoom && fitted && prev) {
    // a step from the picture on screen: move from there, keeping the clicked box in place
    const p = plan(prev.scene, prev.laid, sc, laid, from);
    play(p, drawn, drawGhosts(root, prev.scene, prev.laid, p.leaving.keys(), drawn.firstEdge), state.animate && !reducedMotion.matches ? undefined : 0);
  } else fit(laid.width, laid.height);
  fitted = true;
  const s = byId("status"); s.style.color = "";
  s.textContent = `${scene.stats.nodes} nodes · ${scene.stats.edges} edges · budget ${state.budget} · scene ${scene.stats.ms.scene} ms, round trip ${scene.stats.ms.roundtrip} ms`;
}

function showRawPane() { if (scene) showRaw(scene); byId("status").textContent = ""; }

onRawToggle(toggle);
input("budget").addEventListener("change", () => { state.budget = +input("budget").value; state.expanded = null; refresh(); });
byId("reset").addEventListener("click", () => { state.expanded = null; refresh(); });
byId("collapse").addEventListener("click", () => { state.expanded = ["fs:."]; refresh(); });
for (const [id, k] of Object.entries(OPTIONS)) input(id).addEventListener("change", () => { state[k] = input(id).checked; refresh(true); });
input("nolabels").addEventListener("change", () => { state.noLabels = input("nolabels").checked; byId("g").classList.toggle("nolabels", state.noLabels); writeHash(); });
input("samewidth").addEventListener("change", () => { state.sameWidth = input("samewidth").checked; writeHash(); if (state.view === "diagram") render(true); });
select("theme").addEventListener("change", () => { state.theme = select("theme").value; applyTheme(state.theme); writeHash(); });
input("animate").addEventListener("change", () => { state.animate = input("animate").checked; writeHash(); });
select("direction").addEventListener("change", () => { state.direction = select("direction").value === "RIGHT" ? "RIGHT" : "DOWN"; writeHash(); if (state.view === "diagram") render(); });
themeOptions();
for (const [k, e] of Object.entries(ENGINES)) { const o = document.createElement("option"); o.value = k; o.textContent = e.label; select("engine").appendChild(o); }
select("engine").addEventListener("change", () => { state.engine = select("engine").value; writeHash(); if (state.view === "diagram") render(); });
select("dataset").addEventListener("change", () => { Object.assign(state, DEFAULTS, { name: select("dataset").value }); shown = null; refresh(); });
for (const b of document.querySelectorAll("#views [data-view]")) b.addEventListener("click", () => {
  const v = /** @type {import("./state.js").View} */ (/** @type {HTMLElement} */ (b).dataset.view);
  if (!VIEWS.includes(v) || v === state.view) return;
  state.view = v; syncControls(); writeHash(); show();
});

(async () => {
  /** @type {{ name: string }[]} */
  const sets = await (await fetch("/api/list")).json();
  const sel = select("dataset");
  for (const s of sets) { const o = document.createElement("option"); o.value = s.name; o.textContent = s.name; sel.appendChild(o); }
  const h = readHash(location.hash.slice(1));
  const name = h.name && sets.some(s => s.name === h.name) ? h.name : (sets[0] && sets[0].name);
  if (!name) { byId("status").textContent = "no datasets"; return; }
  sel.value = name;
  Object.assign(state, h.st, { name, noLabels: h.noLabels, sameWidth: h.sameWidth, theme: initialTheme(h.theme), direction: h.direction, engine: ENGINES[h.engine] ? h.engine : "elk", animate: h.animate, view: h.view });
  applyTheme(state.theme);
  await refresh();
})();
