// The card shown next to the pointer over a box or an edge (Info, from nolli/info.py):
// kind and title, where it lives, one sentence, facts as label/value pairs, a list, hints.
// Everything goes in as text (textContent), never as HTML: names come from the repository.

import { byId } from "./dom.js";

/** @typedef {import("../types/nolli").Info} Info @typedef {import("../types/nolli").Parts} Parts */

const tipEl = byId("tip");
const svg = byId("g");

/** @param {string} tag @param {string} [cls] @param {string} [text] */
function el(tag, cls, text) {
  const x = document.createElement(tag);
  if (cls) x.className = cls;
  if (text !== undefined) x.textContent = text;
  return x;
}

/** Prose with values set apart. @param {Parts} parts @param {string} cls */
function sentence(parts, cls) {
  const p = el("div", cls);
  for (const part of parts) p.appendChild(typeof part === "string" ? document.createTextNode(part) : el("span", "v", part.v));
  return p;
}

/** @param {Info} info */
function card(info) {
  const f = document.createDocumentFragment();
  const head = el("div", "head");
  head.appendChild(el("span", "kind", info.kind));
  head.appendChild(el("span", "title", info.title));
  f.appendChild(head);
  if (info.path) f.appendChild(el("div", "path", info.path));
  if (info.summary) f.appendChild(sentence(info.summary, "summary"));
  if (info.facts.length) {
    const dl = el("dl", "facts");
    for (const { label, value } of info.facts) { dl.appendChild(el("dt", "", label)); dl.appendChild(el("dd", "", value)); }
    f.appendChild(dl);
  }
  if (info.list) {
    const box = el("div", "list");
    box.appendChild(el("div", "label", info.list.label));
    const ul = el("ul");
    for (const item of info.list.items) ul.appendChild(el("li", "", item));
    if (info.list.more) ul.appendChild(el("li", "more", `and ${info.list.more} more`));
    box.appendChild(ul);
    f.appendChild(box);
  }
  for (const h of info.hints) f.appendChild(sentence(h, "hint"));
  return f;
}

/** @type {Info | null} the card on show, so moving within one box does not rebuild it */
let shown = null;

/** @param {MouseEvent} ev @param {Info} info */
export function showTip(ev, info) {
  if (info !== shown) { tipEl.replaceChildren(card(info)); shown = info; }
  tipEl.style.display = "block";
  // next to the pointer, flipped to the other side near the canvas's right or bottom edge
  const r = svg.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
  if (x + w > r.width - 8) x = Math.max(8, ev.clientX - r.left - w - 14);
  if (y + h > r.height - 8) y = Math.max(8, ev.clientY - r.top - h - 14);
  tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
}

export function hideTip() { tipEl.style.display = "none"; shown = null; }
