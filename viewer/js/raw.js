// The scene as JSON instead of a drawing, for working on the server side.

import { byId } from "./dom.js";

/** @typedef {import("../types/nolli").Scene} Scene */

const pane = byId("rawpane");
/** @param {string} s */
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

/** @param {Scene} scene */
export function showRaw(scene) {
  pane.style.display = "block";
  const foldable = new Set(scene.nodes.filter(n => "foldable" in n && n.foldable).map(n => n.id));
  // node ids that can be unfolded or folded are clickable, as boxes are in the drawing
  const html = esc(JSON.stringify(scene, null, 1)).replace(/("id": )"([^"]*)"/g, (all, k, id) => foldable.has(id) ? `${k}"<span class="unfold" data-id="${id}">${id}</span>"` : all);
  pane.innerHTML = `<div class="req">GET <a href="${esc(scene.url || "")}" target="_blank">${esc(scene.url || "")}</a> · ${scene.stats.nodes} nodes · ${scene.stats.edges} edges · scene ${scene.stats.ms.scene} ms · round trip ${scene.stats.ms.roundtrip} ms · click an id to unfold/fold it</div><pre>${html}</pre>`;
}

export function hideRaw() { pane.style.display = "none"; }

/** @param {(id: string) => void} fn */
export function onRawToggle(fn) {
  pane.addEventListener("click", ev => { const t = /** @type {HTMLElement | null} */ (/** @type {Element} */ (ev.target).closest(".unfold")); if (t && t.dataset.id) fn(t.dataset.id); });
}
