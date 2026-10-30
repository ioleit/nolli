// The text shown next to the pointer over a box or an edge (Info, from nolli/info.py):
// one paragraph per sentence, the data in it set apart. Everything goes in as text
// (textContent), never as HTML: names come from the repository.

import { byId } from "./dom.js";

/** @typedef {import("../types/nolli").Info} Info */

const tipEl = byId("tip");
const svg = byId("g");

/** @param {Info} info */
function render(info) {
  const f = document.createDocumentFragment();
  for (const line of info) {
    const p = document.createElement("p");
    for (const part of line) {
      if (typeof part === "string") { p.appendChild(document.createTextNode(part)); continue; }
      const v = document.createElement("span"); v.className = "v"; v.textContent = part.v; p.appendChild(v);
    }
    f.appendChild(p);
  }
  return f;
}

/** @type {Info | null} the text on show, so moving within one box does not rebuild it */
let shown = null;

/** @param {MouseEvent} ev @param {Info} info */
export function showTip(ev, info) {
  if (info !== shown) { tipEl.replaceChildren(render(info)); shown = info; }
  tipEl.style.display = "block";
  // next to the pointer, flipped to the other side near the canvas's right or bottom edge
  const r = svg.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
  if (x + w > r.width - 8) x = Math.max(8, ev.clientX - r.left - w - 14);
  if (y + h > r.height - 8) y = Math.max(8, ev.clientY - r.top - h - 14);
  tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
}

export function hideTip() { tipEl.style.display = "none"; shown = null; }
