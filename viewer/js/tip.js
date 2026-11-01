// The text shown for a box or an edge under the pointer (Info, from nolli/info.py): Markdown,
// one paragraph per sentence, the data in it set apart (md.js renders it).
//
// It goes where it hides the least of the picture: of a few spots around the box (or
// around the pointer, for an edge), the one covering the fewest other boxes and edges,
// never the box itself, and above all not what is lit: what it describes glows with what
// goes with it (draw.js, interestOf), and the card keeps clear of all of that.
//
// A card can also be kept open (pinTip): clicking a verdict mark, or going to a review item.
// It stays when the pointer leaves, until Esc, a click on the canvas, a pan or zoom, or
// another card replaces it.

import { byId } from "./dom.js";
import { wasDrag } from "./panzoom.js";
import { render as paragraphs } from "./md.js";

/**
 * @typedef {import("../types/nolli").Info} Info
 * @typedef {{ info: Info, review?: Info }} Card what a card says: about the box or edge, and in a review, about the change (its own part, under a line)
 */

const tipEl = byId("tip");
const svg = byId("g");
const GAP = 12;  // between the text and what it describes

/** @param {Card} card */
function render(card) {
  if (!card.review) return paragraphs(card.info);
  const cols = document.createElement("div"); cols.className = "parts";
  const main = document.createElement("div"); main.className = "main"; main.appendChild(paragraphs(card.info));
  const rev = document.createElement("div"); rev.className = "rev"; rev.appendChild(paragraphs(card.review));
  cols.append(main, rev);
  return cols;
}

/** What lies under some of the picture: box areas, and points along edges. @typedef {{ boxes: DOMRect[], points: { x: number, y: number }[] }} Shapes */

/** Points along an edge's line, in page coordinates, every `step` px on screen. @param {SVGPathElement} path @param {number} step */
function along(path, step) {
  const m = path.getScreenCTM();
  if (!m) return [];
  const len = path.getTotalLength(), out = [];
  for (let d = 0; d <= len; d += step / Math.max(m.a, 0.05)) {
    const q = path.getPointAtLength(d);
    out.push({ x: q.x * m.a + q.y * m.c + m.e, y: q.x * m.b + q.y * m.d + m.f });
  }
  return out;
}

const HEADER = 26;  // an unfolded box's header, its title line

/**
 * What the text should not cover, in page coordinates: every folded box, the header of every
 * unfolded one (its title and changed lines; a card can't stay off all of it), and points along
 * every edge (one per 16 px); `lit`, what is lit, kept apart.
 * @param {Element | null} self the hovered box (not an obstacle to itself)
 * @returns {{ other: Shapes, lit: Shapes }}
 */
function obstacles(self) {
  /** @type {{ other: Shapes, lit: Shapes }} */
  const o = { other: { boxes: [], points: [] }, lit: { boxes: [], points: [] } };
  for (const g of document.querySelectorAll("#root g.node")) {
    if (self && self === g) continue;
    const shape = g.querySelector(":scope > rect, :scope > circle");
    if (!shape) continue;
    const r = shape.getBoundingClientRect(), on = g.classList.contains("lit") ? o.lit : o.other;
    if (g.classList.contains("folded")) on.boxes.push(r);
    else { const k = /** @type {SVGGraphicsElement} */ (shape).getScreenCTM()?.a || 1; on.boxes.push(new DOMRect(r.x, r.y, r.width, Math.min(r.height, HEADER * k))); }
  }
  for (const p of /** @type {NodeListOf<SVGPathElement>} */ (document.querySelectorAll("#root g.edge > path:first-of-type")))
    (p.parentElement?.classList.contains("lit") ? o.lit : o.other).points.push(...along(p, 16));
  return o;
}

/** @param {{ x: number, y: number, w: number, h: number }} t @param {Shapes} o */
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
 * Where to put a w × h text next to one of `arounds` (page coordinates, in order of
 * preference), inside `bounds`, covering as little as possible, much less of what is lit,
 * and never the place it is beside.
 * @param {{ left: number, top: number, right: number, bottom: number }[]} arounds
 * @param {number} w @param {number} h @param {DOMRect} bounds @param {ReturnType<typeof obstacles>} o
 */
function place(arounds, w, h, bounds, o) {
  let best = null, bestScore = Infinity;
  arounds.forEach((around, i) => {
    for (const [j, t] of spotsAround(around, w, h, bounds).entries()) {
      const overSelf = Math.min(t.x + w, around.right) - Math.max(t.x, around.left) > 0 && Math.min(t.y + h, around.bottom) - Math.max(t.y, around.top) > 0;
      const score = covered(t, o.other) + covered(t, o.lit) * 1000 + (overSelf ? 1e9 : 0) + i * 400 + j;
      if (score < bestScore) { best = t; bestScore = score; }
    }
  });
  return best || { x: arounds[0].right + GAP, y: arounds[0].top, w, h };
}

/**
 * The spots for a w × h text around one place, kept inside `bounds`.
 * @param {{ left: number, top: number, right: number, bottom: number }} around
 * @param {number} w @param {number} h @param {DOMRect} bounds
 */
function spotsAround(around, w, h, bounds) {
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
    { x: around.left - GAP - w, y: around.bottom - h },       // left, bottom-aligned
    { x: around.right - w, y: around.top - GAP - h },         // above, right-aligned
  ];
  // keep inside the canvas; a spot pushed back over what it describes scores badly in place()
  return spots.map(s => ({ x: Math.min(Math.max(s.x, bounds.left + 8), bounds.right - w - 8),
                          y: Math.min(Math.max(s.y, bounds.top + 8), bounds.bottom - h - 8), w, h }));
}

/** Points along a drawn edge's line, every 12 px on screen. @param {Element} ge */
function edgePoints(ge) {
  const path = /** @type {SVGPathElement | null} */ (ge.querySelector(":scope > path"));
  return path ? along(path, 12) : [];
}

/** @type {Card | null} the card on show, so moving within one box does not move it */
let shown = null;
let pinned = false;

/** @typedef {{ left: number, top: number, right: number, bottom: number }} Around */

/**
 * Show a card beside one of `arounds` (page coordinates, preferred first).
 * @param {Card} card @param {Around[]} arounds @param {Element | null} self the box it describes
 */
function put(card, arounds, self) {
  tipEl.replaceChildren(render(card)); shown = card;
  tipEl.classList.toggle("two", !!card.review);
  tipEl.style.display = "block";
  const canvas = svg.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  const t = place(arounds, w, h, canvas, obstacles(self));
  tipEl.style.left = (t.x - canvas.left) + "px"; tipEl.style.top = (t.y - canvas.top) + "px";
}

/** A small square around a point. @param {{ x: number, y: number }} p @returns {Around} */
const near = p => ({ left: p.x - 6, right: p.x + 6, top: p.y - 6, bottom: p.y + 6 });

/**
 * Where a card about a drawn thing may go beside: a box's shape; for an edge, places along
 * its line from the middle outwards (the line is lit, so the card stays off it).
 * @param {Element} x @returns {{ arounds: Around[], self: Element | null }}
 */
function aroundOf(x) {
  if (!x.matches("g.edge")) {
    const shape = x.querySelector(":scope > rect, :scope > circle");
    return { arounds: [(shape || x).getBoundingClientRect()], self: x };
  }
  const own = edgePoints(x);
  if (!own.length) return { arounds: [x.getBoundingClientRect()], self: null };
  const at = (/** @type {number} */ f) => near(own[Math.min(own.length - 1, Math.round(f * (own.length - 1)))]);
  return { arounds: [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8].map(at), self: null };
}

/**
 * @param {MouseEvent} ev
 * @param {Card} card a scene node or edge, or a card made for the occasion
 * @param {Element | null} [box] the hovered box, the text goes beside it; or the hovered edge, the
 *   text goes beside the pointer or else elsewhere along the line; without, beside the pointer
 * Whatever is lit when it is called, the card keeps clear of.
 */
export function showTip(ev, card, box = null) {
  if (card === shown) return;
  pinned = false;
  const pointer = near({ x: ev.clientX, y: ev.clientY });
  if (box && !box.matches("g.edge")) { const a = aroundOf(box); put(card, a.arounds, a.self); return; }
  // an edge: beside the pointer if it can, else beside some other part of the line, else beside
  // the boxes lit with it (its two ends)
  const ends = [...document.querySelectorAll("#root g.node.folded.lit > rect")].map(r => r.getBoundingClientRect());
  put(card, [pointer, ...box ? aroundOf(box).arounds : [], ...ends], null);
}

/**
 * Show a card beside a drawn box or edge and keep it there when the pointer leaves.
 * @param {Card} card @param {Element} target
 */
export function pinTip(card, target) {
  const a = aroundOf(target);
  put(card, a.arounds, a.self);
  pinned = true;
}

/** Hide the card; one kept open (pinTip) only when forced. @param {boolean} [force] */
export function hideTip(force = false) {
  if (pinned && !force) return;
  tipEl.style.display = "none"; shown = null; pinned = false;
}

window.addEventListener("keydown", ev => { if (ev.key === "Escape") hideTip(true); });
svg.addEventListener("click", () => { if (!wasDrag()) hideTip(true); });
svg.addEventListener("wheel", () => hideTip(true), { passive: true });
svg.addEventListener("mousemove", ev => { if (pinned && ev.buttons) hideTip(true); });  // a pan
