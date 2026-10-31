// Drawing: scene + layout -> SVG. Rebuilds everything inside the root group, and hands
// back a handle per drawn node so an animation can move it (update) and fade it.

import { showTip, hideTip, pinTip } from "./tip.js";
import { ICON_GAP, ICON_H, USES_LINE, iconW, iconsW, isAnchor, statsText } from "./layout.js";
import { wasDrag } from "./panzoom.js";
import { glyph } from "./verdicts.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").SceneNode} SceneNode
 * @typedef {import("../types/nolli").SceneEdge} SceneEdge
 * @typedef {import("../types/nolli").BoxNode} BoxNode
 * @typedef {import("../types/nolli").Laid} Laid
 * @typedef {import("../types/nolli").Box} Box
 * @typedef {{ els: SVGElement[], update: (b: Box) => void }} Built
 * @typedef {{ update: (b: Box) => void, opacity: (o: number | null) => void }} Handle
 * @typedef {{ nodes: Map<string, Handle>, edges: SVGElement[], firstEdge: Element | null }} Drawn
 * @typedef {{ toggle: (id: string) => void, review?: boolean }} Handlers
 *   review: a diff scene, where what did not change is drawn as such
 */


/** A verdict marker: a small disc with a glyph, centred at (x, y). @param {string} verdict @param {number} x @param {number} y */
function verdictMark(verdict, x, y) {
  const g = el("g", { class: "verdict " + verdict, transform: `translate(${x},${y})` });
  g.appendChild(el("circle", { r: 7 }));
  const t = el("text", { y: 3.5, "text-anchor": "middle" }); t.textContent = glyph(verdict); if (t.textContent.length > 1) g.classList.add("two"); g.appendChild(t);
  return g;
}

const PALETTE = ["#4a90e2", "#e2874a", "#3a9d5d", "#b05cc9", "#d9534f", "#2aa8a8", "#c9a227", "#7a7a7a"];
/** @param {number} i */
const color = i => PALETTE[(i < 0 ? 7 : i) % PALETTE.length];
const ns = "http://www.w3.org/2000/svg";
/** @param {string} t @param {Record<string, string | number>} [a] @returns {SVGElement} */
const el = (t, a = {}) => { const x = /** @type {SVGElement} */ (document.createElementNS(ns, t)); for (const [k, v] of Object.entries(a)) x.setAttribute(k, String(v)); return x; };
// Arrowheads: one size for every edge (in drawing units, so they zoom with the boxes),
// drawn as part of the edge so they share its colour, opacity and hover (viewer.css); the
// line stops at the head's base so a thick line can't show through the point.
const HEAD = 9, HALF = 3.6;  // length, half width

/** The head at the end of a route, as polygon points. @param {{ x: number, y: number }[]} pts */
function head(pts) {
  const n = pts.length, a = pts[n - 2], b = pts[n - 1], len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len, l = Math.min(HEAD, len * 0.9);
  const bx = b.x - ux * l, by = b.y - uy * l;
  return `${b.x},${b.y} ${bx - uy * HALF},${by + ux * HALF} ${bx + uy * HALF},${by - ux * HALF}`;
}

/** The route with its last segment shortened by the head's length. @param {{ x: number, y: number }[]} pts */
function shortened(pts) {
  const n = pts.length; if (n < 2) return pts;
  const a = pts[n - 2], b = pts[n - 1], len = Math.hypot(b.x - a.x, b.y - a.y);
  const cut = Math.min(HEAD, len * 0.9);
  if (!len) return pts;
  return [...pts.slice(0, -1), { x: b.x - (b.x - a.x) / len * cut, y: b.y - (b.y - a.y) / len * cut }];
}
/** @param {Box} b */
const at = b => `translate(${b.x},${b.y})`;

/** @param {SceneEdge} e @param {boolean} review */
function edgeClass(e, review) {
  return ["edge", e.local && "local", e.single && "single", e.weak && "weak", e.style, e.heuristic && "heuristic", e.types && "types",
    e.diff ? "d-" + e.diff.status : review && "d-same", e.verdict && "v-" + e.verdict].filter(Boolean).join(" ");
}

/**
 * A verdict mark opens the hover card of what it sits on: hovering shows it, clicking keeps it
 * (until Esc, a click on the canvas, a pan, or another card).
 * @param {SVGElement} mark @param {import("./tip.js").Card} card @param {Element} target what the card goes beside
 */
function cardOnMark(mark, card, target) {
  mark.addEventListener("mouseenter", ev => showTip(/** @type {MouseEvent} */ (ev), card, target));
  mark.addEventListener("mouseleave", () => hideTip());
  mark.addEventListener("mousedown", ev => ev.stopPropagation());  // don't start a pan
  mark.addEventListener("click", ev => { ev.stopPropagation(); pinTip(card, target); });
}

/** Opacity as the SVG attribute (null removes it). @param {SVGElement[]} els @returns {(o: number | null) => void} */
const fader = els => o => { for (const x of els) if (o === null) x.removeAttribute("opacity"); else x.setAttribute("opacity", String(o)); };

/**
 * A module's own code: a dot that edges attach to.
 * @param {import("../types/nolli").AnchorNode} n @param {Box} p
 * @returns {Built}
 */
function buildAnchor(n, p) {
  const grp = el("g", { class: "node folded files ", transform: at(p), "data-id": n.id });
  grp.appendChild(el("circle", { cx: 7, cy: 7, r: 5, class: "anchor" }));
  return { els: [grp], update: b => grp.setAttribute("transform", at(b)) };
}

/** CSS class part for an icon: letters kept, "/" is "path". @param {string} icon */
const iconClass = icon => icon === "/" ? "path" : icon.replace(/[^A-Za-z]/g, "");

/**
 * A box: folded (a leaf) or unfolded (a container whose children are drawn separately).
 * Built at box p; update(b) moves and resizes it, recomputing everything that depends on
 * its size (centred label, badges, the − button).
 * @param {BoxNode} n @param {Box} p @param {Handlers | null} h null: not interactive
 * @returns {Built}
 */
function buildBox(n, p, h) {
  const onToggle = h ? h.toggle : null;
  const isC = n.container;
  const grp = el("g", { class: ["node", isC ? "container" : "folded", n.kind,
    n.diff ? "d-" + n.diff.status : h && h.review ? "d-same" : "", n.verdict ? "v-" + n.verdict : ""].join(" "), transform: at(p), "data-id": n.id });
  const rect = el("rect", { width: p.w, height: p.h }); grp.appendChild(rect);
  const icons = n.icons;
  const centred = !isC && !icons.length;
  let ix = isC ? 7 : 6;
  for (const icon of icons) {
    const w = iconW(icon);
    const ig = el("g", { class: "icon i-" + iconClass(icon), transform: `translate(${ix}, ${isC ? 7 : 9})` });
    ig.appendChild(el("rect", { width: w, height: ICON_H, rx: 3 }));
    const it = el("text", { x: w / 2, y: 9.5, "text-anchor": "middle" }); it.textContent = icon; ig.appendChild(it);
    grp.appendChild(ig);
    ix += w + ICON_GAP;
  }
  const t = el("text", { x: centred ? p.w / 2 : ix + 1, y: isC ? 18 : 19, "text-anchor": centred ? "middle" : "start", "font-weight": isC ? "600" : "400" });
  t.textContent = n.label; grp.appendChild(t);
  // container header: title, then badges right-aligned (truncated to the room left), then the
  // fold button; a container narrower than its title gets the button right after the title
  const titleW = n.label.length * 7.2 + 8 + iconsW(icons);
  /** @param {number} w */
  const foldX = w => (isC && w < titleW + 40) ? titleW + 10 : w - 14;
  /** @type {SVGElement | null} */
  let top = null, foldRect = null, foldText = null;
  if (isC) {
    // only the − folds a container; clicks elsewhere on it must not interfere with navigation
    // a group with a transparent hit area: node text has pointer-events off, and the glyph alone is a tiny target
    const fx = foldX(p.w);
    const f = el("g", { class: "fold" });
    foldRect = el("rect", { x: fx - 9, y: 5, width: 18, height: 18, rx: 3 }); f.appendChild(foldRect);
    foldText = el("text", { x: fx, y: 18, "text-anchor": "middle" }); foldText.textContent = "−"; f.appendChild(foldText);
    f.appendChild(el("title")).textContent = "fold";
    if (onToggle) {
      f.addEventListener("mousedown", ev => ev.stopPropagation());  // don't start a pan
      f.addEventListener("click", ev => { ev.stopPropagation(); onToggle(n.id); });
    }
    // drawn above the edges (an edge's hover strip would otherwise swallow the click)
    top = el("g", { class: "node", transform: at(p), "data-fold": n.id }); top.appendChild(f);
  }
  if (n.utility) grp.classList.add("utility");
  /** @type {SVGElement | null} */
  let leaves = null, badge = null;
  if (!isC && n.leaves) { leaves = el("text", { class: "badge", x: p.w - 5, y: 10, "text-anchor": "end" }); leaves.textContent = "+" + n.leaves; grp.appendChild(leaves); }
  // changed lines: after the title in a container's header, on the second line of a folded box
  const stats = statsText(n), statsW = stats ? stats.length * 6 + 8 : 0;
  /** @type {SVGElement | null} */
  let statsEl = null;
  if (stats && n.diff) {
    statsEl = el("text", { class: "stats", x: isC ? titleW + 4 : 6 + n.chips.length * 11, y: isC ? 18 : 33 });
    const a = el("tspan", { class: "plus" }); a.textContent = `+${n.diff.added}`; statsEl.appendChild(a);
    const r = el("tspan", { class: "minus", dx: 4 }); r.textContent = `−${n.diff.removed}`; statsEl.appendChild(r);
    grp.appendChild(statsEl);
  }
  /** the badge text that fits a container w wide @param {number} w */
  const fitted = w => { if (!isC || !n.badge) return n.badge; const room = Math.floor((w - 36 - titleW - statsW) / 5.4); return room < 6 ? "" : n.badge.length > room ? n.badge.slice(0, room - 1) + "…" : n.badge; };
  /** @param {number} w */
  const badgeX = w => isC ? w - 28 : stats ? w - 6 : (w + n.chips.length * 11) / 2;
  if (fitted(p.w)) { badge = el("text", { class: "badge", x: badgeX(p.w), y: isC ? 18 : 33, "text-anchor": isC || stats ? "end" : "middle" }); badge.textContent = fitted(p.w); if (isC) badge.appendChild(el("title")).textContent = n.badge; grp.appendChild(badge); }
  // the review's verdict on what this box contains, on its top-right corner; "fine" only in the panel
  /** @type {SVGElement | null} */
  let mark = null;
  if (n.verdict && n.verdict !== "fine") { mark = verdictMark(n.verdict, p.w - 1, 1); if (h) cardOnMark(mark, n, grp); }
  n.chips.forEach((c, i) => { const ch = el("circle", { class: "chip", cx: 9 + i * 11, cy: isC ? 14 : 32, r: 4, fill: color(c.color), "stroke-width": 1 }); ch.appendChild(el("title")).textContent = "deployed in " + c.name; grp.appendChild(ch); });
  // an unfolded box lists the utilities its children use, one per line, bottom right
  /** @type {SVGElement[]} */
  const uses = isC ? (n.uses || []).map(u => { const x = el("text", { class: "badge uses", "text-anchor": "end" }); x.textContent = "→ " + u; grp.appendChild(x); return x; }) : [];
  /** @param {Box} b */
  const placeUses = b => uses.forEach((x, i) => { x.setAttribute("x", String(b.w - 8)); x.setAttribute("y", String(b.h - 8 - (uses.length - 1 - i) * USES_LINE)); });
  placeUses(p);
  if (n.service_color !== null && n.service_color !== undefined) rect.setAttribute("stroke", color(n.service_color));
  if (onToggle && !isC && n.foldable) grp.addEventListener("click", ev => { if (wasDrag()) return; ev.stopPropagation(); onToggle(n.id); });

  /** @param {Box} b */
  const update = b => {
    grp.setAttribute("transform", at(b));
    rect.setAttribute("width", String(b.w)); rect.setAttribute("height", String(b.h));
    placeUses(b);
    if (centred) t.setAttribute("x", String(b.w / 2));
    if (top && foldRect && foldText) { const fx = foldX(b.w); top.setAttribute("transform", at(b)); foldRect.setAttribute("x", String(fx - 9)); foldText.setAttribute("x", String(fx)); }
    if (leaves) leaves.setAttribute("x", String(b.w - 5));
    if (badge) { badge.setAttribute("x", String(badgeX(b.w))); if (isC) badge.firstChild && (badge.firstChild.textContent = fitted(b.w)); }
    if (mark) mark.setAttribute("transform", `translate(${b.x + b.w - 1},${b.y + 1})`);
  };
  if (mark) { mark.setAttribute("transform", `translate(${p.x + p.w - 1},${p.y + 1})`); mark.setAttribute("data-id", n.id); }
  const extra = [top, mark].filter(/** @returns {x is SVGElement} */ x => x !== null);
  return { els: [grp, ...extra], update };
}

/** @param {SceneNode} n @param {Box} p @param {Handlers | null} h */
const build = (n, p, h) => isAnchor(n) ? buildAnchor(n, p) : buildBox(n, p, h);

/**
 * @param {Element} g the root group
 * @param {Scene} scene
 * @param {Laid} laid
 * @param {((id: string) => void) | Handlers} handlers unfold or fold a node
 * @returns {Drawn}
 */
export function draw(g, scene, laid, handlers) {
  /** @type {Handlers} */
  const h = typeof handlers === "function" ? { toggle: handlers } : handlers;
  const review = !!h.review;
  g.innerHTML = "";
  hideTip(true);  // a card kept open belongs to the old drawing
  /** @type {SVGElement[]} */
  const foldLayer = [];
  /** @type {Map<string, Handle>} */
  const nodes = new Map();
  /** @type {Map<string, Element>} */
  const boxEl = new Map();

  // scene nodes come in drawing order: a container before what it contains
  for (const n of scene.nodes) {
    const p = laid.nodes.get(n.id);
    if (!p) continue;
    const { els, update } = build(n, p, h);
    g.appendChild(els[0]);
    foldLayer.push(...els.slice(1));
    nodes.set(n.id, { update, opacity: fader(els) });
    boxEl.set(n.id, els[0]);
  }

  /** @type {SVGElement[]} */
  const edges = [];
  /** @type {Map<string, SVGElement[]>} the edges at each node */
  const edgesAt = new Map();
  // a glowing edge goes on top of the other edges (still below the fold buttons), so its glow isn't covered
  /** @param {Element} ge */
  const raise = ge => {
    const after = foldLayer[0] && foldLayer[0].parentNode === g ? foldLayer[0] : null;
    if (ge.nextSibling !== after) g.insertBefore(ge, after);
  };
  for (const { edge: e, sections } of laid.edges) {
    const ge = el("g", { class: edgeClass(e, review), "data-key": e.key });
    /** @type {{ x: number, y: number } | null} */
    let mid = null;
    for (const pts of sections) {
      const d = "M" + pts.map(p => `${p.x},${p.y}`).join("L");
      const line = "M" + shortened(pts).map(p => `${p.x},${p.y}`).join("L");
      ge.appendChild(el("path", { d: line, "stroke-width": 1.2 }));  // every edge one width, whatever its count
      if (pts.length > 1) ge.appendChild(el("polygon", { class: "head", points: head(pts) }));
      ge.appendChild(el("path", { d, style: "stroke:transparent;stroke-width:9px;fill:none" }));
      const a = pts[Math.floor((pts.length - 1) / 2)], b = pts[Math.ceil((pts.length - 1) / 2)]; mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }
    if (e.verdict && e.verdict !== "fine" && mid) { const vm = verdictMark(e.verdict, mid.x - 9, mid.y); cardOnMark(vm, e, ge); ge.appendChild(vm); }
    ge.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), e, null, ge));
    ge.addEventListener("mouseenter", () => raise(ge));
    ge.addEventListener("mouseleave", () => hideTip());
    g.appendChild(ge); edges.push(ge);
    for (const id of [e.src, e.dst]) { const l = edgesAt.get(id); if (l) l.push(ge); else edgesAt.set(id, [ge]); }
  }
  foldLayer.forEach(f => g.appendChild(f));
  raiseEdge = raise;
  lightOnHover(scene, boxEl, edgesAt, raise);
  return { nodes, edges, firstEdge: edges[0] || foldLayer[0] || null };
}

/** @type {(ge: Element) => void} the last drawing's way of putting an edge on top of the others */
let raiseEdge = () => {};
/** @type {ReturnType<typeof setTimeout> | null} */
let flashTimer = null;

/**
 * Light boxes and edges from outside the drawing: the review list points at an item while
 * its row is hovered, or flashes it after a jump (a few pulses of the hover glow, then
 * nothing stays lit). Same .lit as hovering a box.
 * @param {Element} g the root group @param {string[]} ids box ids @param {string[]} keys edge keys @param {boolean} [flash]
 */
export function light(g, ids, keys, flash = false) {
  unlight();
  if (flashTimer) { clearTimeout(flashTimer); flashTimer = null; }
  for (const id of ids) g.querySelectorAll(`.node[data-id="${CSS.escape(id)}"]`).forEach(x => lit.add(x));
  for (const k of keys) g.querySelectorAll(`.edge[data-key="${CSS.escape(k)}"]`).forEach(x => { lit.add(x); raiseEdge(x); });
  for (const x of lit) { x.classList.add("lit"); if (flash) x.classList.add("flash"); }
  if (flash) flashTimer = setTimeout(() => { flashTimer = null; unlight(); }, 2400);
}

/**
 * Hovering a leaf (a folded box, or a module's own-code dot) lights it up (.lit, viewer.css),
 * with its incoming and outgoing edges and the utilities it uses. Unfolded boxes don't light.
 * @param {Scene} scene @param {Map<string, Element>} boxEl @param {Map<string, SVGElement[]>} edgesAt
 * @param {(ge: Element) => void} raise
 */
function lightOnHover(scene, boxEl, edgesAt, raise) {
  unlight();  // a redraw starts dark
  for (const n of scene.nodes) {
    const box = boxEl.get(n.id);
    if (!box || ("container" in n && n.container)) continue;
    // the hover text appears with the highlight: for leaves only, on the same event
    box.addEventListener("mouseenter", ev => {
      showTip(/** @type {MouseEvent} */ (ev), n, box);
      unlight();
      lit.add(box);
      for (const u of n.utilities_used || []) { const b = boxEl.get(u); if (b) lit.add(b); }
      for (const ge of edgesAt.get(n.id) || []) { lit.add(ge); raise(ge); }
      for (const x of lit) x.classList.add("lit");
    });
    box.addEventListener("mouseleave", () => { unlight(); hideTip(); });
  }
}

/** What is lit now: one set for the whole page, so a missed mouseleave can't leave anything lit. @type {Set<Element>} */
const lit = new Set();
function unlight() { for (const x of lit) x.classList.remove("lit", "flash"); lit.clear(); }
document.getElementById("g")?.addEventListener("mouseleave", unlight);

/**
 * Non-interactive copies of nodes that are about to leave the picture, drawn below the
 * edges at their old boxes so an animation can shrink them away; remove() takes them out.
 * @param {Element} g @param {Scene} scene the scene they were drawn from
 * @param {Laid} laid their boxes in that scene @param {Iterable<string>} ids @param {Element | null} before
 */
export function drawGhosts(g, scene, laid, ids, before) {
  const want = new Set(ids);
  /** @type {Map<string, Handle>} */
  const nodes = new Map();
  /** @type {SVGElement[]} */
  const all = [];
  for (const n of scene.nodes) {
    const p = laid.nodes.get(n.id);
    if (!p || !want.has(n.id)) continue;
    const { els, update } = build(n, p, null);
    for (const x of els) { x.setAttribute("pointer-events", "none"); g.insertBefore(x, before); all.push(x); }
    nodes.set(n.id, { update, opacity: fader(els) });
  }
  return { nodes, remove: () => all.forEach(x => x.remove()) };
}
