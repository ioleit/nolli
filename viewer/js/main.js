// The viewer only lays out and draws. Which boxes exist, which edges connect them, and
// every label, badge and emphasis flag come from the server: /api/scene (nolli/scene.py).
//
//   state.js    view state, URL hash, scene query
//   layout/     scene -> positions and edge routes, no DOM; one module per engine
//   draw.js     scene + layout -> SVG, with a handle per node to move and fade it
//   transition.js, animate.js: the step between two pictures (plan, then play)
//   raw.js      scene -> JSON view;  mermaid.js: the static export as a view
//   review.js   diff datasets: the review panel, items, details, verdicts
//   sidebar.js, help.js (explanations on hover), panzoom.js, tip.js, dom.js
//   stability.js, bench.js (bench.html): how well layouts keep the picture across a step
// Types: types/nolli.d.ts, checked with `npm run check`.

import { byId, input, select } from "./dom.js";
import { ALL_ROLES, DEFAULTS, OPTIONS, VIEWS, query, readHash, state, writeHash } from "./state.js";
import { ENGINES, layout } from "./layout/index.js";
import { draw, drawGhosts, light } from "./draw.js";
import { plan } from "./transition.js";
import { finish, play } from "./animate.js";
import { hideRaw, onRawToggle, showRaw } from "./raw.js";
import { sidebar, syncControls } from "./sidebar.js";
import { fit, focusOn } from "./panzoom.js";
import { hideMermaid, showMermaid } from "./mermaid.js";
import "./help.js";
import { applyTheme, initialTheme, themeOptions } from "./theme.js";
import { itemById, loadItems, renderPanel, scrollToCurrent, showPanel, visibleItems } from "./review.js";

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
/** after the next drawing, centre on the item gone to and flash it */
let focusNext = false;
/** @type {string | null} the dataset whose review items are loaded */
let itemsFor = null;
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
  state.expanded = sc.state.expanded; state.roles = sc.state.roles; state.groups = sc.state.groups; state.budget = sc.state.budget; state.utilities = sc.state.utilities;
  syncControls(); sidebar(sc, () => refresh(true));
  if (sc.diff) {
    if (itemsFor !== state.name) { await loadItems(state.name || ""); itemsFor = state.name; }
    renderPanel(sc, reviewHandlers);
  } else showPanel(false);
  document.body.classList.toggle("dim", !!sc.diff && state.dim);
  await show(keepZoom && !focusNext);
  writeHash();
}

/** Bring the current view up to date: the diagram, the scene as JSON, or the Mermaid export. */
async function show(keepZoom = false) {
  if (state.view !== "mermaid") hideMermaid();
  if (state.view === "json") showRawPane();
  else if (state.view === "mermaid") { hideRaw(); byId("status").textContent = ""; if (state.name) await showMermaid(state.name); }
  else await render(keepZoom);
}

// ---------- review: going to an item ----------

/** @type {import("./review.js").ReviewHandlers} */
const reviewHandlers = {
  jump: id => jump(id),
  hover: id => { if (!scene || !shown) return; const t = id ? targets(id) : { ids: [], keys: [] }; light(byId("root"), t.ids, t.keys); },
  side: s => { state.side = s; refresh(true); },
  dim: on => { state.dim = on; document.body.classList.toggle("dim", on); writeHash(); },
  reload: () => { itemsFor = null; refresh(true); },
  roles: all => { state.roles = all ? [...ALL_ROLES] : null; refresh(true); },
};

/** The drawn boxes and edges that show an item (scene.nodes[].items, scene.edges[].items). @param {string} id */
function targets(id) {
  if (!scene) return { ids: [], keys: [] };
  return { ids: scene.nodes.filter(n => "items" in n && n.items?.includes(id)).map(n => n.id), keys: scene.edges.filter(e => e.items?.includes(id)).map(e => e.key) };
}

/** Go to an item: unfold what it needs, then (once drawn) centre on it and flash it. @param {string} id */
function jump(id) {
  const it = itemById(id);
  if (!it) return;
  state.item = id;
  const have = new Set(state.expanded || []);
  const missing = it.reveal.filter(x => !have.has(x));
  focusNext = true;
  if (missing.length) { state.expanded = [...have, ...missing]; refresh(true); return; }
  if (scene) renderPanel(scene, reviewHandlers);
  pointAtItem();
  writeHash();
}

/** After a jump: centre on the item's boxes and edges and flash them. */
function pointAtItem() {
  if (!focusNext || !scene || !shown || !state.item) { focusNext = false; return; }
  focusNext = false;
  const { ids, keys } = targets(state.item);
  const laid = shown.laid;
  const ends = scene.edges.filter(e => keys.includes(e.key)).flatMap(e => [e.src, e.dst]);
  focusOn([...ids, ...ends].map(x => laid.nodes.get(x)).filter(/** @returns {b is import("../types/nolli").Box} */ b => !!b));
  light(byId("root"), ids, keys, true);
  scrollToCurrent();
}

/** @param {number} step +1 next, -1 previous */
function stepItem(step) {
  const list = visibleItems();
  if (!list.length) return;
  const cur = state.item ? list.findIndex(it => it.id === state.item) : -1;
  const next = cur < 0 ? (step > 0 ? 0 : list.length - 1) : (cur + step + list.length) % list.length;
  jump(list[next].id);
}

window.addEventListener("keydown", ev => {
  const t = /** @type {HTMLElement} */ (ev.target);
  if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA") || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (!scene?.diff && ev.key !== "f") return;
  if (ev.key === "j" || ev.key === "n") stepItem(1);
  else if (ev.key === "k" || ev.key === "p") stepItem(-1);
  else if (ev.key === "1") reviewHandlers.side("base");
  else if (ev.key === "2") reviewHandlers.side("both");
  else if (ev.key === "3") reviewHandlers.side("head");
  else if (ev.key === "f" && shown) fit(shown.laid.width, shown.laid.height);
  else return;
  ev.preventDefault();
});

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
  const drawn = draw(root, sc, laid, { toggle, review: !!sc.diff }, state.sameWidth);
  shown = { scene: sc, laid };
  if (keepZoom && fitted && prev) {
    // a step from the picture on screen: move from there, keeping the clicked box in place
    const p = plan(prev.scene, prev.laid, sc, laid, from);
    play(p, drawn, drawGhosts(root, prev.scene, prev.laid, p.leaving.keys(), drawn.firstEdge), state.animate && !reducedMotion.matches ? undefined : 0);
  } else fit(laid.width, laid.height);
  fitted = true;
  pointAtItem();
  const s = byId("status"); s.style.color = "";
  s.textContent = `${scene.stats.nodes} nodes · ${scene.stats.edges} edges · budget ${state.budget} · scene ${scene.stats.ms.scene} ms, round trip ${scene.stats.ms.roundtrip} ms`;
}

function showRawPane() { if (scene) showRaw(scene); byId("status").textContent = ""; }

onRawToggle(toggle);
input("utilities").addEventListener("change", () => { state.utilities = Math.max(0, Math.round(+input("utilities").value || 0)); refresh(true); });
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
select("dataset").addEventListener("change", () => { Object.assign(state, DEFAULTS, { name: select("dataset").value, item: null }); shown = null; refresh(); });
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
  Object.assign(state, h.st, { name, noLabels: h.noLabels, sameWidth: h.sameWidth, theme: initialTheme(h.theme), direction: h.direction, engine: ENGINES[h.engine] ? h.engine : "elk", animate: h.animate, view: h.view,
    item: h.item, dim: h.dim });
  applyTheme(state.theme);
  await refresh();
  // a link to an item: unfold what it needs (the hash may come from a different view state)
  const loaded = /** @type {Scene | null} */ (scene);  // set by refresh(); TypeScript cannot see that through the await
  if (h.item && loaded?.diff) jump(h.item);
})();
