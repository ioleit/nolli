// The text shown for a box or an edge under the pointer (Info, from nolli/info.py): one
// paragraph per sentence, the data in it set apart. Everything goes in as text
// (textContent), never as HTML: names come from the repository.
//
// It goes where it hides the least of the picture: of a few spots around the box (or
// around the pointer, for an edge), the one covering the fewest other boxes and edges,
// never the box itself. It glows like what it describes.

import { byId } from "./dom.js";

/** @typedef {import("../types/nolli").Info} Info */

const tipEl = byId("tip");
const svg = byId("g");
const GAP = 12;  // between the text and what it describes

/** @param {Info} info */
function render(info) {
  const f = document.createDocumentFragment();
  for (const line of info) {
    const p = document.createElement("p");
    for (const part of line) {
      if (typeof part === "string") { p.appendChild(document.createTextNode(part)); continue; }
      if ("doc" in part) { const q = document.createElement("q"); q.className = "doc"; q.textContent = part.doc; p.appendChild(q); continue; }
      const v = document.createElement("span"); v.className = "v"; v.textContent = part.v; p.appendChild(v);
    }
    f.appendChild(p);
  }
  return f;
}

/**
 * What the text should not cover, in page coordinates: every folded box, and points
 * along every edge (one per 16 px).
 * @param {Element | null} self the hovered box (not an obstacle to itself)
 * @returns {{ boxes: DOMRect[], points: { x: number, y: number }[] }}
 */
function obstacles(self) {
  const boxes = [...document.querySelectorAll("#root g.node.folded > rect, #root g.node.folded > circle")]
    .filter(r => !self || !self.contains(r)).map(r => r.getBoundingClientRect());
  /** @type {{ x: number, y: number }[]} */
  const points = [];
  for (const p of /** @type {NodeListOf<SVGPathElement>} */ (document.querySelectorAll("#root g.edge > path:first-of-type"))) {
    const m = p.getScreenCTM();
    if (!m) continue;
    const len = p.getTotalLength();
    for (let d = 0; d <= len; d += 16 / Math.max(m.a, 0.2)) {
      const q = p.getPointAtLength(d);
      points.push({ x: q.x * m.a + q.y * m.c + m.e, y: q.x * m.b + q.y * m.d + m.f });
    }
  }
  return { boxes, points };
}

/** @param {{ x: number, y: number, w: number, h: number }} t @param {ReturnType<typeof obstacles>} o */
function covered(t, o) {
  let area = 0;
  for (const b of o.boxes) {
    const w = Math.min(t.x + t.w, b.right) - Math.max(t.x, b.left), h = Math.min(t.y + t.h, b.bottom) - Math.max(t.y, b.top);
    if (w > 0 && h > 0) area += w * h;
  }
  let pts = 0;
  for (const p of o.points) if (p.x > t.x && p.x < t.x + t.w && p.y > t.y && p.y < t.y + t.h) pts++;
  return area + pts * 120;  // an edge point counts like a small patch of box
}

/**
 * Where to put a w × h text next to `around` (page coordinates), inside `bounds`.
 * @param {{ left: number, top: number, right: number, bottom: number }} around
 * @param {number} w @param {number} h @param {DOMRect} bounds @param {ReturnType<typeof obstacles>} o
 */
function place(around, w, h, bounds, o) {
  const cx = (around.left + around.right) / 2, cy = (around.top + around.bottom) / 2;
  const spots = [  // in order of preference when they hide equally little
    { x: around.right + GAP, y: around.top },                 // right, top-aligned
    { x: around.left, y: around.bottom + GAP },               // below, left-aligned
    { x: around.right + GAP, y: cy - h / 2 },                 // right, centred
    { x: around.left - GAP - w, y: around.top },              // left
    { x: around.left, y: around.top - GAP - h },              // above
    { x: cx - w / 2, y: around.bottom + GAP },                // below, centred
    { x: around.right + GAP, y: around.bottom - h },          // right, bottom-aligned
    { x: around.left - GAP - w, y: cy - h / 2 },              // left, centred
  ];
  let best = null, bestScore = Infinity;
  for (const s of spots) {
    // keep inside the canvas; a spot pushed back over what it describes is no good
    const x = Math.min(Math.max(s.x, bounds.left + 8), bounds.right - w - 8);
    const y = Math.min(Math.max(s.y, bounds.top + 8), bounds.bottom - h - 8);
    const t = { x, y, w, h };
    const overSelf = Math.min(x + w, around.right) - Math.max(x, around.left) > 0 && Math.min(y + h, around.bottom) - Math.max(y, around.top) > 0;
    const score = covered(t, o) + (overSelf ? 1e9 : 0);
    if (score < bestScore) { best = t; bestScore = score; }
  }
  return best || { x: around.right + GAP, y: around.top, w, h };
}

/** @type {Info | null} the text on show, so moving within one box does not move it */
let shown = null;

/**
 * @param {MouseEvent} ev
 * @param {Info} info
 * @param {Element | null} [box] the hovered box: the text goes beside it; without, beside the pointer
 */
export function showTip(ev, info, box = null) {
  if (info === shown) return;
  tipEl.replaceChildren(render(info)); shown = info;
  tipEl.style.display = "block";
  const canvas = svg.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  const shape = box && box.querySelector(":scope > rect, :scope > circle");
  const around = shape ? shape.getBoundingClientRect()
    : { left: ev.clientX - 6, right: ev.clientX + 6, top: ev.clientY - 6, bottom: ev.clientY + 6 };
  const t = place(around, w, h, canvas, obstacles(box));
  tipEl.style.left = (t.x - canvas.left) + "px"; tipEl.style.top = (t.y - canvas.top) + "px";
}

export function hideTip() { tipEl.style.display = "none"; shown = null; }
