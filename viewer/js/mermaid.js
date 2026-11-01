// The Mermaid tab: the scene as Mermaid (/api/mermaid, nolli/mermaid.py), rendered by
// Mermaid itself, and its source to copy. Clicking a box unfolds or folds it, as in the drawing.

import { byId } from "./dom.js";

/** @typedef {import("../types/nolli").Scene} Scene */

const pane = byId("mmdpane");
/** @type {string} the URL whose picture is shown */
let rendered = "";
/** @type {string} its source */
let source = "";
/** @type {Map<string, string>} Mermaid node id -> scene id of the boxes in the picture shown that unfold or fold */
let clickable = new Map();
/** @type {string} the id the picture shown was rendered under: Mermaid's element ids start with it */
let diagram = "";

/** @param {string} s */
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

/**
 * @param {Scene} scene the scene shown in the other tabs (for the ids that can be clicked)
 * @param {string} url /api/mermaid with the same scene state (the text; the tab asks for it with its ids)
 */
export async function showMermaid(scene, url) {
  pane.style.display = "block";
  if (rendered === url) return;
  rendered = url;
  const t0 = performance.now();
  const res = await fetch(url + "&format=json");
  /** @type {{ mermaid: string, ids: Record<string, string> } | { error: string }} */
  const body = await res.json();
  if (rendered !== url) return;  // a newer state asked for meanwhile
  if (!("mermaid" in body)) { pane.innerHTML = `<pre class="error">${esc(body.error)}</pre>`; return; }
  const src = body.mermaid;
  try {
    const mermaid = (await import("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs")).default;
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", maxTextSize: 400000, maxEdges: 4000 });
    const id = "mmd" + Date.now();
    const { svg } = await mermaid.render(id, src);
    if (rendered !== url) return;
    source = src; diagram = id;
    const foldable = new Set(scene.nodes.filter(n => "foldable" in n && n.foldable).map(n => n.id));
    clickable = new Map(Object.entries(body.ids).filter(([, x]) => foldable.has(x)));
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
 * Mermaid ids the elements of a diagram rendered as <diagram>: a node "<diagram>-flowchart-<node id>-<n>",
 * a subgraph "<diagram>-<subgraph id>"; the node ids are the server's (/api/mermaid, format=json).
 * @param {Element} g
 */
function sceneId(g) {
  const node = diagram + "-flowchart-", sub = diagram + "-";
  const id = g.id.startsWith(node) ? g.id.slice(node.length).replace(/-\d+$/, "") : g.id.startsWith(sub) ? g.id.slice(sub.length) : "";
  return clickable.get(id) || null;
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
