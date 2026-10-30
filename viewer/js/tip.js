// The text shown for a box or an edge under the pointer (Info, from nolli/info.py): one
// paragraph per sentence, the data in it set apart. Everything goes in as text
// (textContent), never as HTML: names come from the repository.
//
// It goes where it hides the least of the picture: of a few spots around the box (or
// around the pointer, for an edge), the one covering the fewest other boxes and edges,
// never the box itself. It glows like what it describes.
//
// A card can also be kept open (pinTip): clicking a verdict mark, or going to a review item.
// It stays when the pointer leaves, until Esc, a click on the canvas, a pan or zoom, or
// another card replaces it.

import { byId } from "./dom.js";
import { wasDrag } from "./panzoom.js";

/**
 * @typedef {import("../types/nolli").Info} Info
 * @typedef {{ info: Info, review?: Info }} Card what a card says: about the box or edge, and in a review, about the change (its own part, under a line)
 */

const tipEl = byId("tip");
const svg = byId("g");
const GAP = 12;  // between the text and what it describes
const VERDICT_GLYPH = { fine: "✓", question: "?", problem: "!" };

/** @param {Card} card */
function render(card) {
  if (!card.review?.length) return paragraphs(card.info);
  const cols = document.createElement("div"); cols.className = "parts";
  const main = document.createElement("div"); main.className = "main"; main.appendChild(paragraphs(card.info));
  const rev = document.createElement("div"); rev.className = "rev"; rev.appendChild(paragraphs(card.review));
  cols.append(main, rev);
  return cols;
}

/** @param {Info} info */
function paragraphs(info) {
  const f = document.createDocumentFragment();
  for (const line of info) {
    const p = document.createElement("p");
    for (const part of line) {
      if (typeof part === "string") { p.appendChild(document.createTextNode(part)); continue; }
      if ("verdict" in part) { const b = document.createElement("span"); b.className = "vd " + part.verdict; b.textContent = VERDICT_GLYPH[part.verdict] || "•"; p.appendChild(b); continue; }
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
 * Where to put a w × h text next to one of `arounds` (page coordinates, in order of
 * preference), inside `bounds`, covering as little as possible and, above all, not what it
 * describes: the box, or the points along the edge (`own`).
 * @param {{ left: number, top: number, right: number, bottom: number }[]} arounds
 * @param {number} w @param {number} h @param {DOMRect} bounds @param {ReturnType<typeof obstacles>} o
 * @param {{ x: number, y: number }[]} own
 */
function place(arounds, w, h, bounds, o, own) {
  let best = null, bestScore = Infinity;
  arounds.forEach((around, i) => {
    for (const [j, t] of spotsAround(around, w, h, bounds).entries()) {
      let hit = 0;
      for (const p of own) if (p.x > t.x && p.x < t.x + t.w && p.y > t.y && p.y < t.y + t.h) hit++;
      const overSelf = Math.min(t.x + w, around.right) - Math.max(t.x, around.left) > 0 && Math.min(t.y + h, around.bottom) - Math.max(t.y, around.top) > 0;
      const score = covered(t, o) + hit * 1e5 + (overSelf ? 1e9 : 0) + i * 400 + j;
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
  ];
  // keep inside the canvas; a spot pushed back over what it describes scores badly in place()
  return spots.map(s => ({ x: Math.min(Math.max(s.x, bounds.left + 8), bounds.right - w - 8),
                          y: Math.min(Math.max(s.y, bounds.top + 8), bounds.bottom - h - 8), w, h }));
}

/** Points along an edge's line, in page coordinates, every 12 px on screen. @param {Element} ge */
function edgePoints(ge) {
  const path = /** @type {SVGPathElement | null} */ (ge.querySelector(":scope > path"));
  const m = path && path.getScreenCTM();
  if (!path || !m) return [];
  const len = path.getTotalLength(), out = [];
  for (let d = 0; d <= len; d += 12 / Math.max(m.a, 0.05)) {
    const q = path.getPointAtLength(d);
    out.push({ x: q.x * m.a + q.y * m.c + m.e, y: q.x * m.b + q.y * m.d + m.f });
  }
  return out;
}

/** @type {Card | null} the card on show, so moving within one box does not move it */
let shown = null;
let pinned = false;

/** @typedef {{ left: number, top: number, right: number, bottom: number }} Around */

/**
 * Show a card beside one of `arounds` (page coordinates, preferred first).
 * @param {Card} card @param {Around[]} arounds @param {Element | null} self the box it describes
 * @param {{ x: number, y: number }[]} [own] points along the edge it describes
 */
function put(card, arounds, self, own = []) {
  tipEl.replaceChildren(render(card)); shown = card;
  tipEl.classList.toggle("two", !!card.review?.length);
  tipEl.style.display = "block";
  const canvas = svg.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  const t = place(arounds, w, h, canvas, obstacles(self), own);
  tipEl.style.left = (t.x - canvas.left) + "px"; tipEl.style.top = (t.y - canvas.top) + "px";
}

/** A small square around a point. @param {{ x: number, y: number }} p @returns {Around} */
const near = p => ({ left: p.x - 6, right: p.x + 6, top: p.y - 6, bottom: p.y + 6 });

/**
 * Where a card about a drawn thing may go beside: a box's shape; for an edge, places along
 * its line from the middle outwards (the card then stays off the whole line).
 * @param {Element} x @returns {{ arounds: Around[], own: { x: number, y: number }[], self: Element | null }}
 */
function aroundOf(x) {
  if (!x.matches("g.edge")) {
    const shape = x.querySelector(":scope > rect, :scope > circle");
    return { arounds: [(shape || x).getBoundingClientRect()], own: [], self: x };
  }
  const own = edgePoints(x);
  if (!own.length) return { arounds: [x.getBoundingClientRect()], own, self: null };
  const at = (/** @type {number} */ f) => near(own[Math.min(own.length - 1, Math.round(f * (own.length - 1)))]);
  return { arounds: [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8].map(at), own, self: null };
}

/**
 * @param {MouseEvent} ev
 * @param {Card} card a scene node or edge, or a card made for the occasion
 * @param {Element | null} [box] the hovered box or edge: the text goes beside it; without, beside the pointer
 * @param {Element | null} [edge] an edge hovered at the pointer: the text goes beside the pointer, off the edge's line
 */
export function showTip(ev, card, box = null, edge = null) {
  if (card === shown) return;
  pinned = false;
  if (box) { const a = aroundOf(box); put(card, a.arounds, a.self, a.own); return; }
  put(card, [near({ x: ev.clientX, y: ev.clientY })], null, edge ? edgePoints(edge) : []);
}

/**
 * Show a card beside a drawn box or edge and keep it there when the pointer leaves.
 * @param {Card} card @param {Element} target
 */
export function pinTip(card, target) {
  const a = aroundOf(target);
  put(card, a.arounds, a.self, a.own);
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
