// The Mermaid tab: the scene as Mermaid (/api/mermaid, nolli/mermaid.py), rendered by
// Mermaid itself, and its source to copy. Clicking a box unfolds or folds it, as in the drawing.

import { byId } from "./dom.js";

/** @typedef {import("../types/nolli").Scene} Scene */

const pane = byId("mmdpane");
/** @type {string} the URL whose picture is shown */
let rendered = "";
/** @type {string} its source */
let source = "";
/** @type {Map<string, string>} Mermaid node id (n_…, a subgraph's n_…_g) -> scene id, for the picture shown */
let ids = new Map();

/** @param {string} s */
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
/** The Mermaid node id of a scene id, as nolli/mermaid.py writes it. @param {string} id */
const nid = id => "n_" + id.replace(/[^A-Za-z0-9]/g, "_");

/**
 * @param {Scene} scene the scene shown in the other tabs (for the ids that can be clicked)
 * @param {string} url /api/mermaid with the same scene state
 */
export async function showMermaid(scene, url) {
  pane.style.display = "block";
  if (rendered === url) return;
  rendered = url;
  const t0 = performance.now();
  const res = await fetch(url);
  const src = await res.text();
  if (rendered !== url) return;  // a newer state asked for meanwhile
  if (!res.ok) { pane.innerHTML = `<pre class="error">${esc(src)}</pre>`; return; }
  try {
    const mermaid = (await import("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs")).default;
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", maxTextSize: 400000, maxEdges: 4000 });
    const { svg } = await mermaid.render("mmd" + Date.now(), src);
    if (rendered !== url) return;
    source = src;
    ids = new Map(scene.nodes.filter(n => "foldable" in n && n.foldable).flatMap(n => [[nid(n.id), n.id], [nid(n.id) + "_g", n.id]]));
    const ms = Math.round(performance.now() - t0);
    pane.innerHTML = `<div class="req">GET <a href="${esc(url)}" target="_blank">${esc(url)}</a> · ${src.split("\n").length} lines · ${ms} ms
      · <button class="link" id="mmd-copy">copy Mermaid</button> · click a box to unfold or fold it</div>
      <div class="mmd-svg">${svg}</div>
      <details class="mmd-src"><summary>source</summary><pre>${esc(src)}</pre></details>`;
    byId("mmd-copy").addEventListener("click", ev => {
      const b = /** @type {HTMLElement} */ (ev.currentTarget);
      navigator.clipboard.writeText(source).then(() => { b.textContent = "copied"; setTimeout(() => { b.textContent = "copy Mermaid"; }, 1500); });
    });
    for (const g of pane.querySelectorAll(".mmd-svg g.node, .mmd-svg g.cluster")) if (sceneId(g)) g.classList.add("unfold");
  } catch (e) { pane.innerHTML = `<pre class="error">${esc(String(e))}</pre><pre>${esc(src)}</pre>`; }
}

/**
 * The scene id a drawn Mermaid node or subgraph stands for, if it can be unfolded or folded.
 * Mermaid ids its nodes "<diagram>-flowchart-<node id>-<n>" and its subgraphs "<diagram>-<subgraph id>".
 * @param {Element} g
 */
function sceneId(g) {
  const m = /-flowchart-(n_\w+?)-\d+$/.exec(g.id) || /-(n_\w+_g)$/.exec(g.id);
  return m && ids.get(m[1]) || null;
}

export function hideMermaid() { pane.style.display = "none"; }

/** @param {(id: string) => void} fn */
export function onMermaidToggle(fn) {
  pane.addEventListener("click", ev => {
    const g = /** @type {Element} */ (ev.target).closest(".mmd-svg g.node, .mmd-svg g.cluster");
    const id = g && sceneId(g);
    if (id) fn(id);
  });
}
