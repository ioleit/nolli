// The viewer only lays out and draws. Which boxes exist, which edges connect them, and
// every label, badge and emphasis flag come from the server: /api/scene (nolli/scene.py).
//
//   state.js    view state, URL hash, scene query
//   layout.js   scene -> positions and edge routes (ELK), no DOM
//   draw.js     scene + layout -> SVG, with a handle per node to move and fade it
//   transition.js, animate.js: the step between two pictures (plan, then play)
//   raw.js      scene -> JSON view;  mermaid.js: the static export as a view
//   review.js   diff datasets: the review panel, items, details, verdicts
//   sidebar.js, help.js (explanations on hover), panzoom.js, tip.js, dom.js
//   stability.js, bench.js (bench.html): how well layouts keep the picture across a step
// Types: types/nolli.d.ts, checked with `npm run check`.

import { byId, input, select } from "./dom.js";
import { ALL_ROLES, DEFAULTS, VIEWS, query, readHash, state, writeHash } from "./state.js";
import { layout } from "./layout.js";
import { draw, drawGhosts, light } from "./draw.js";
import { hideTip, pinTip } from "./tip.js";
import { plan } from "./transition.js";
import { DURATION, finish, play } from "./animate.js";
import { hideRaw, onRawToggle, showRaw } from "./raw.js";
import { sidebar, syncControls } from "./sidebar.js";
import { fit, focusOn } from "./panzoom.js";
import { hideMermaid, showMermaid } from "./mermaid.js";
import "./help.js";
import { applyTheme, chooseTheme, initialTheme, themeOptions } from "./theme.js";
import { itemById, loadItems, renderPanel, scrollToCurrent, showPanel, visibleItems } from "./review.js";

/** @typedef {import("../types/nolli").Scene} Scene @typedef {import("../types/nolli").Laid} Laid */

// surface failures instead of a silently blank canvas
/** @param {string} msg */
const showError = msg => {
  const s = byId("status"); s.textContent = "error: " + msg; s.style.color = "#d9534f";
  document.body.classList.remove("busy");
  if (!shown) { loading("error: " + msg); byId("loading").classList.add("error"); }
};

const loadingDataset = () => loading(`loading ${state.name} …`, "reading the model and working out the picture");

/**
 * What the canvas says until the first picture is drawn; null once it is.
 * @param {string | null} what @param {string} [why] a second, smaller line
 */
function loading(what, why = "") {
  const el = byId("loading");
  el.hidden = what === null;
  el.classList.remove("error");
  el.replaceChildren(...(what === null ? [] : [what, ...(why ? [Object.assign(document.createElement("div"), { className: "why", textContent: why })] : [])]));
}
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
  // the server reads the model on the first request and works out the picture on each; a
  // big repository takes seconds for either
  if (!shown) loadingDataset();
  else { byId("status").style.color = ""; byId("status").textContent = "updating …"; }
  document.body.classList.add("busy");
  const res = await fetch(url);
  const body = await res.json();
  if (my !== seq) return;  // a newer request is under way
  document.body.classList.remove("busy");
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
  if (state.view !== "diagram") loading(null);  // the other views cover the canvas
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
  unfold: ids => { state.expanded = [...new Set([...(state.expanded || []), ...ids])]; refresh(true); },
};

/** The drawn boxes and edges that show a change or carry a verdict (scene.nodes[].items / .verdicts,
 * the same on edges). @param {string} id */
function targets(id) {
  if (!scene) return { ids: [], keys: [] };
  const on = (/** @type {{ items?: string[], verdicts?: string[] }} */ x) => (id.startsWith("V") ? x.verdicts : x.items)?.includes(id);
  return { ids: scene.nodes.filter(n => "items" in n && on(n)).map(n => n.id), keys: scene.edges.filter(on).map(e => e.key) };
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

const GLIDE = 520;  // ms, the move to an item after a jump

/** After a jump, one step after the other: let the redraw finish moving boxes (if it does),
 * glide to the item's boxes and edges while they flash, then open the card beside them.
 * @param {boolean} [moving] the drawing is still moving into place */
function pointAtItem(moving = false) {
  if (!focusNext || !scene || !shown || !state.item) { focusNext = false; return; }
  focusNext = false;
  if (pinTimer) clearTimeout(pinTimer);
  const id = state.item;
  scrollToCurrent();
  hideTip(true);  // a card kept open for the last item would hang in the air while the view moves
  const go = () => {
    pinTimer = null;
    if (!scene || !shown || state.item !== id) return;
    const { ids, keys } = targets(id);
    const laid = shown.laid;
    const ends = scene.edges.filter(e => keys.includes(e.key)).flatMap(e => [e.src, e.dst]);
    const smooth = !reducedMotion.matches;
    const ms = focusOn([...ids, ...ends].map(x => laid.nodes.get(x)).filter(/** @returns {b is import("../types/nolli").Box} */ b => !!b), smooth ? GLIDE : 0);
    light(byId("root"), ids, keys, true);
    pinTimer = setTimeout(() => { pinTimer = null; pinItem(id); }, ms + 30);
  };
  if (moving) pinTimer = setTimeout(go, DURATION + 30); else go();
}

/** @type {ReturnType<typeof setTimeout> | null} */
let pinTimer = null;

/**
 * Keep open the card of what shows a change or carries a verdict (an edge for a dependency
 * or a verdict on an edge, else a box). A verdict's card leads with it; a change's with its
 * title.
 * @param {string} id
 */
function pinItem(id) {
  const it = itemById(id);
  if (!scene || !it || state.item !== id) return;
  const { ids, keys } = targets(id);
  const root = byId("root");
  const edge = scene.edges.find(e => e.key === keys[0]), node = scene.nodes.find(n => n.id === ids[0]);
  const useEdge = edge && ("edge" in it ? it.edge : it.category === "dependency") || !node;
  const target = useEdge ? edge && root.querySelector(`.edge[data-key="${CSS.escape(edge.key)}"]`) : root.querySelector(`.node[data-id="${CSS.escape(node.id)}"]`);
  const shown = useEdge ? edge : node;
  if (!target || !shown) return;
  /** @type {import("../types/nolli").Info} */
  const review = shown.review || [];
  if ("verdict" in it) {
    // the card already has this verdict's line; move it to the top
    const mine = (/** @type {import("../types/nolli").Parts} */ l) => l.some(x => typeof x === "object" && "verdict" in x) && l.includes(it.reason.split("`")[0]);
    pinTip({ info: shown.info, review: [[{ verdict: it.verdict }, " ", ...marked(it.reason)], ...review.filter(l => !mine(l))] }, target);
  } else pinTip({ info: shown.info, review: [[{ v: it.title }], ...review] }, target);
}

/** Text with `code` spans as parts, the spans set apart (as nolli/review.py marked). @param {string} text */
const marked = text => text.split("`").map((t, i) => i % 2 ? { v: t } : t).filter(t => typeof t !== "string" || t);

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
  if (!shown) loading(`laying out ${sc.stats.nodes} boxes and ${sc.stats.edges} edges …`);
  const laid = await layout(sc);
  if (sc !== scene) return;  // a newer scene arrived while this one was laid out
  finish();
  const root = byId("root");
  const drawn = draw(root, sc, laid, { toggle, review: !!sc.diff });
  shown = { scene: sc, laid };
  loading(null);
  if (keepZoom && fitted && prev) {
    // a step from the picture on screen: move from there, keeping the clicked box in place
    const p = plan(prev.scene, prev.laid, sc, laid, from);
    play(p, drawn, drawGhosts(root, prev.scene, prev.laid, p.leaving.keys(), drawn.firstEdge), reducedMotion.matches ? 0 : undefined);
  } else fit(laid.width, laid.height);
  fitted = true;
  pointAtItem(keepZoom && !!prev && !reducedMotion.matches);
  const s = byId("status"); s.style.color = "";
  s.textContent = `${scene.stats.nodes} nodes · ${scene.stats.edges} edges · budget ${state.budget} · scene ${scene.stats.ms.scene} ms, round trip ${scene.stats.ms.roundtrip} ms`;
}

function showRawPane() { if (scene) showRaw(scene); byId("status").textContent = ""; }

onRawToggle(toggle);
input("utilities").addEventListener("change", () => { state.utilities = Math.max(0, Math.round(+input("utilities").value || 0)); refresh(true); });
input("budget").addEventListener("change", () => { state.budget = +input("budget").value; state.expanded = null; refresh(); });
byId("reset").addEventListener("click", () => { state.expanded = null; refresh(); });
byId("collapse").addEventListener("click", () => { state.expanded = ["fs:."]; refresh(); });
select("theme").addEventListener("change", () => { state.theme = select("theme").value; chooseTheme(state.theme); writeHash(); });
themeOptions();
select("dataset").addEventListener("change", async () => {
  Object.assign(state, DEFAULTS, { name: select("dataset").value, item: null }); shown = null;
  byId("root").replaceChildren();  // the last dataset's picture would stand behind the loading message
  await enter();
});

/**
 * Open the dataset in state.name. A review with verdicts starts folded and goes to the
 * first (worst) verdict: the picture as a whole, then the first thing to look at. Anything
 * else, or a link that says what to show, starts as it says.
 * @param {string | null} [item] an item named in the link
 */
async function enter(item = null) {
  loadingDataset();
  const res = state.expanded === null && !item ? await fetch("/api/changes?d=" + encodeURIComponent(state.name || "")) : null;
  const first = res && res.ok ? (await res.json()).verdicts.length > 0 : false;
  if (first) state.expanded = ["fs:."];
  await refresh();
  const loaded = /** @type {Scene | null} */ (scene);  // set by refresh(); TypeScript cannot see that through the await
  if (!loaded?.diff) return;
  const to = item || (first ? visibleItems()[0]?.id : null);
  if (to) jump(to);
}
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
  if (!name) { loading("no datasets"); return; }
  sel.value = name;
  Object.assign(state, h.st, { name, theme: initialTheme(h.theme), view: h.view, item: h.item, dim: h.dim });
  applyTheme(state.theme);
  // a link to an item unfolds what it needs (the hash may come from a different view state)
  await enter(h.item);
})();
