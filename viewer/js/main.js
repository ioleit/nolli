// The viewer only lays out and draws. Which boxes exist, which edges connect them, and
// every label, badge and emphasis flag come from the server: /api/scene (nolli/scene.py).
//
//   state.js    view state, URL hash, scene query
//   layout.js   scene -> positions and edge routes (ELK), no DOM
//   draw.js     scene + layout -> SVG
//   raw.js      scene -> JSON pane
//   sidebar.js, panzoom.js, tip.js, mermaid.js

import { DEFAULTS, OPTIONS, query, readHash, state, writeHash } from "./state.js";
import { layout } from "./layout.js";
import { draw } from "./draw.js";
import { hideRaw, onRawToggle, showRaw } from "./raw.js";
import { sidebar, syncControls } from "./sidebar.js";
import { fit } from "./panzoom.js";
import { toggleMermaid } from "./mermaid.js";

const $ = id => document.getElementById(id);
// surface failures instead of a silently blank canvas
const showError = msg => { const s = $("status"); s.textContent = "error: " + msg; s.style.color = "#d9534f"; };
window.addEventListener("error", ev => showError(ev.message + (ev.lineno ? ` (${(ev.filename || "").split("/").pop()}:${ev.lineno})` : "")));
window.addEventListener("unhandledrejection", ev => showError(String(ev.reason && ev.reason.message || ev.reason)));

let scene = null;  // the last scene received
let seq = 0;
let fitted = false;

async function refresh(keepZoom = false) {
  const my = ++seq, url = "/api/scene?" + query(), t0 = performance.now();
  const res = await fetch(url);
  const body = await res.json();
  if (my !== seq) return;  // a newer request is under way
  if (!res.ok) { showError(body.error || res.status); return; }
  body.stats.ms.roundtrip = Math.round(performance.now() - t0);
  scene = body; scene.url = url;
  // adopt the server's normalised state (default cut resolved, folded descendants dropped)
  state.expanded = scene.state.expanded; state.roles = scene.state.roles; state.budget = scene.state.budget;
  syncControls(); sidebar(scene, toggle, () => refresh());
  if (state.raw) showRawPane(); else await render(keepZoom);
  writeHash();
}

function toggle(id) {
  const x = new Set(state.expanded);
  x.has(id) ? x.delete(id) : x.add(id);  // the server drops whatever was unfolded inside a folded box
  state.expanded = [...x]; refresh(true);
}

async function render(keepZoom = false) {
  hideRaw();
  const laid = await layout(scene, state.direction);
  draw($("root"), scene, laid, toggle);
  if (!keepZoom || !fitted) fit(laid.width, laid.height);
  fitted = true;
  const s = $("status"); s.style.color = "";
  s.textContent = `${scene.stats.nodes} nodes · ${scene.stats.edges} edges · budget ${state.budget} · scene ${scene.stats.ms.scene} ms, round trip ${scene.stats.ms.roundtrip} ms`;
}

function showRawPane() { showRaw(scene); $("status").textContent = ""; }

onRawToggle(toggle);
$("budget").addEventListener("change", ev => { state.budget = +ev.target.value; state.expanded = null; refresh(); });
$("reset").addEventListener("click", () => { state.expanded = null; refresh(); });
$("collapse").addEventListener("click", () => { state.expanded = ["fs:."]; refresh(); });
for (const [id, k] of Object.entries(OPTIONS)) $(id).addEventListener("change", ev => { state[k] = ev.target.checked; refresh(true); });
$("nolabels").addEventListener("change", ev => { state.noLabels = ev.target.checked; $("g").classList.toggle("nolabels", state.noLabels); writeHash(); });
$("raw").addEventListener("change", ev => { state.raw = ev.target.checked; writeHash(); if (state.raw) showRawPane(); else render(); });
$("direction").addEventListener("change", ev => { state.direction = ev.target.value; writeHash(); render(); });
$("dataset").addEventListener("change", ev => { Object.assign(state, DEFAULTS, { name: ev.target.value }); refresh(); });
$("mmd").addEventListener("click", () => toggleMermaid(state.name));

(async () => {
  const sets = await (await fetch("/api/list")).json();
  const sel = $("dataset");
  for (const s of sets) { const o = document.createElement("option"); o.value = s.name; o.textContent = s.name; sel.appendChild(o); }
  const h = readHash(location.hash.slice(1));
  const name = h.name && sets.some(s => s.name === h.name) ? h.name : (sets[0] && sets[0].name);
  if (!name) { $("status").textContent = "no datasets"; return; }
  sel.value = name;
  Object.assign(state, h.st, { name, noLabels: h.noLabels, direction: h.direction, raw: h.raw });
  await refresh();
  if (h.mmd) $("mmd").click();
})();
