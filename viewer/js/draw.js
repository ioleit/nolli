// Drawing: scene + layout -> SVG. Rebuilds everything inside the root group, and hands
// back a handle per drawn node so an animation can move it (update) and fade it.

import { showTip, hideTip } from "./tip.js";
import { isAnchor, statsText } from "./layout/index.js";
import { wasDrag } from "./panzoom.js";

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
 * @typedef {{ toggle: (id: string) => void, select?: (what: string) => void, review?: boolean }} Handlers
 *   select: "node:<id>" or "edge:<key>"; review: a diff scene, where what did not change is drawn as such
 */

const VERDICT_GLYPH = { fine: "✓", question: "?", problem: "!" };

/** A verdict marker: a small disc with a glyph, centred at (x, y). @param {string} verdict @param {number} x @param {number} y */
function verdictMark(verdict, x, y) {
  const g = el("g", { class: "verdict " + verdict, transform: `translate(${x},${y})` });
  g.appendChild(el("circle", { r: 7 }));
  const t = el("text", { y: 3.5, "text-anchor": "middle" }); t.textContent = VERDICT_GLYPH[/** @type {"fine" | "question" | "problem"} */ (verdict)] || "•"; g.appendChild(t);
  return g;
}

const PALETTE = ["#4a90e2", "#e2874a", "#3a9d5d", "#b05cc9", "#d9534f", "#2aa8a8", "#c9a227", "#7a7a7a"];
/** @param {number} i */
const color = i => PALETTE[(i < 0 ? 7 : i) % PALETTE.length];
const ns = "http://www.w3.org/2000/svg";
/** @param {string} t @param {Record<string, string | number>} [a] @returns {SVGElement} */
const el = (t, a = {}) => { const x = /** @type {SVGElement} */ (document.createElementNS(ns, t)); for (const [k, v] of Object.entries(a)) x.setAttribute(k, String(v)); return x; };
const MARKERS = `<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#999"/></marker><marker id="arr-hollow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="Canvas" stroke="#2aa8a8" stroke-width="1"/></marker>`;
/** @param {Box} b */
const at = b => `translate(${b.x},${b.y})`;

/** @param {SceneEdge} e @param {boolean} review */
function edgeClass(e, review) {
  return ["edge", e.local && "local", e.single && "single", e.weak && "weak", e.style, e.dangling && "dangling", e.heuristic && "heuristic",
    e.diff ? "d-" + e.diff.status : review && "d-same", e.verdict && "v-" + e.verdict].filter(Boolean).join(" ");
}

/** Opacity as the SVG attribute (null removes it). @param {SVGElement[]} els @returns {(o: number | null) => void} */
const fader = els => o => { for (const x of els) if (o === null) x.removeAttribute("opacity"); else x.setAttribute("opacity", String(o)); };

/**
 * A module's own code: a dot that edges attach to.
 * @param {import("../types/nolli").AnchorNode} n @param {Box} p @param {boolean} live with tooltip
 * @returns {Built}
 */
function buildAnchor(n, p, live) {
  const grp = el("g", { class: "node folded files ", transform: at(p) });
  if (live) { grp.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), n.tip)); grp.addEventListener("mouseleave", hideTip); }
  grp.appendChild(el("circle", { cx: 7, cy: 7, r: 5, class: "anchor" }));
  return { els: [grp], update: b => grp.setAttribute("transform", at(b)) };
}

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
  const grp = el("g", { class: ["node", isC ? "container" : "folded", n.kind, n.cycle ? "cycle" : "",
    n.diff ? "d-" + n.diff.status : h && h.review ? "d-same" : "", n.verdict ? "v-" + n.verdict : ""].join(" "), transform: at(p), "data-id": n.id });
  if (onToggle) { grp.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), n.tip)); grp.addEventListener("mouseleave", hideTip); }
  const rect = el("rect", { width: p.w, height: p.h }); grp.appendChild(rect);
  const icon = n.icon;
  const centred = !isC && !icon;
  if (icon) { const ig = el("g", { class: "icon " + icon, transform: `translate(${isC ? 7 : 6}, ${isC ? 7 : 9})` }); ig.appendChild(el("rect", { width: 12, height: 12, rx: 3 })); const it = el("text", { x: 6, y: 9.5, "text-anchor": "middle" }); it.textContent = icon; ig.appendChild(it); grp.appendChild(ig); }
  const t = el("text", { x: isC ? (icon ? 24 : 8) : (icon ? 22 : p.w / 2), y: isC ? 18 : 19, "text-anchor": isC || icon ? "start" : "middle", "font-weight": isC ? "600" : "400" });
  t.textContent = n.label; grp.appendChild(t);
  // container header: title, then badges right-aligned (truncated to the room left), then the
  // fold button; a container narrower than its title gets the button right after the title
  const titleW = n.label.length * 7.2 + 8 + (icon ? 16 : 0);
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
    top = el("g", { class: "node", transform: at(p) }); top.appendChild(f);
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
  if (n.verdict && n.verdict !== "fine") { mark = verdictMark(n.verdict, p.w - 1, 1); mark.appendChild(el("title")).textContent = `${n.verdict}: ${(n.items || []).join(", ")}`; }
  n.chips.forEach((c, i) => { const ch = el("circle", { cx: 9 + i * 11, cy: isC ? 14 : 32, r: 4, fill: color(c.color), stroke: "Canvas", "stroke-width": 1 }); ch.appendChild(el("title")).textContent = "deployed in " + c.name; grp.appendChild(ch); });
  if (n.service_color !== null && n.service_color !== undefined) rect.setAttribute("stroke", color(n.service_color));
  // click: select (the review panel shows it) and unfold a folded box; alt-click only selects.
  // a container is selected by its header strip, so clicks inside it stay free for what it holds
  /** @type {SVGElement | null} */
  let header = null;
  if (h) {
    const target = isC ? (header = el("rect", { class: "header", width: p.w, height: 26 })) : grp;
    if (header) grp.insertBefore(header, rect.nextSibling);
    target.addEventListener("click", ev => {
      if (wasDrag()) return;
      ev.stopPropagation();
      if (h.select) h.select("node:" + n.id);
      if (!isC && n.foldable && !/** @type {MouseEvent} */ (ev).altKey && onToggle) onToggle(n.id);
    });
  }

  /** @param {Box} b */
  const update = b => {
    grp.setAttribute("transform", at(b));
    rect.setAttribute("width", String(b.w)); rect.setAttribute("height", String(b.h));
    if (centred) t.setAttribute("x", String(b.w / 2));
    if (top && foldRect && foldText) { const fx = foldX(b.w); top.setAttribute("transform", at(b)); foldRect.setAttribute("x", String(fx - 9)); foldText.setAttribute("x", String(fx)); }
    if (leaves) leaves.setAttribute("x", String(b.w - 5));
    if (badge) { badge.setAttribute("x", String(badgeX(b.w))); if (isC) badge.firstChild && (badge.firstChild.textContent = fitted(b.w)); }
    if (mark) mark.setAttribute("transform", `translate(${b.x + b.w - 1},${b.y + 1})`);
    if (header) header.setAttribute("width", String(b.w));
  };
  if (mark) { mark.setAttribute("transform", `translate(${p.x + p.w - 1},${p.y + 1})`); mark.setAttribute("data-id", n.id); }
  const extra = [top, mark].filter(/** @returns {x is SVGElement} */ x => x !== null);
  return { els: [grp, ...extra], update };
}

/** @param {SceneNode} n @param {Box} p @param {Handlers | null} h */
const build = (n, p, h) => isAnchor(n) ? buildAnchor(n, p, !!h) : buildBox(n, p, h);

/**
 * @param {Element} g the root group
 * @param {Scene} scene
 * @param {Laid} laid
 * @param {((id: string) => void) | Handlers} handlers unfold or fold a node, and optionally select one
 * @returns {Drawn}
 */
export function draw(g, scene, laid, handlers) {
  /** @type {Handlers} */
  const h = typeof handlers === "function" ? { toggle: handlers } : handlers;
  const review = !!h.review;
  g.innerHTML = "";
  const defs = el("defs"); defs.innerHTML = MARKERS; g.appendChild(defs);
  /** @type {SVGElement[]} */
  const foldLayer = [];
  /** @type {Map<string, Handle>} */
  const nodes = new Map();

  // scene nodes come in drawing order: a container before what it contains
  for (const n of scene.nodes) {
    const p = laid.nodes.get(n.id);
    if (!p) continue;
    const { els, update } = build(n, p, h);
    g.appendChild(els[0]);
    foldLayer.push(...els.slice(1));
    nodes.set(n.id, { update, opacity: fader(els) });
  }

  /** @type {SVGElement[]} */
  const edges = [];
  for (const { edge: e, sections } of laid.edges) {
    const ge = el("g", { class: edgeClass(e, review), "data-key": e.key });
    /** @type {{ x: number, y: number } | null} */
    let mid = null;
    for (const pts of sections) {
      const d = "M" + pts.map(p => `${p.x},${p.y}`).join("L");
      ge.appendChild(el("path", { d, "marker-end": e.kind === "provide" ? "url(#arr-hollow)" : "url(#arr)", "stroke-width": Math.min(2.6, 0.8 + 0.4 * Math.log2(e.count)) }));
      ge.appendChild(el("path", { d, style: "stroke:transparent;stroke-width:9px;fill:none" }));
      const a = pts[Math.floor((pts.length - 1) / 2)], b = pts[Math.ceil((pts.length - 1) / 2)]; mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }
    // in a review a changed edge says how its references changed; unchanged ones stay quiet
    const label = e.diff ? (e.diff.status === "changed" ? `${e.diff.base}→${e.diff.head}` : e.diff.status === "added" ? `+${e.diff.head}` : `−${e.diff.base}`)
      : review ? "" : e.label;
    if (label && mid && (e.diff || (!e.weak && !e.local))) { const t = el("text", { x: mid.x + 3, y: mid.y - 3 }); t.textContent = label; ge.appendChild(t); }
    if (e.verdict && e.verdict !== "fine" && mid) { const vm = verdictMark(e.verdict, mid.x - 9, mid.y); vm.appendChild(el("title")).textContent = `${e.verdict}: ${(e.items || []).join(", ")}`; ge.appendChild(vm); }
    ge.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), e.tip));
    ge.addEventListener("mouseleave", hideTip);
    if (h.select) { const sel = h.select; ge.addEventListener("click", ev => { if (wasDrag()) return; ev.stopPropagation(); sel("edge:" + e.key); }); }
    g.appendChild(ge); edges.push(ge);
  }
  foldLayer.forEach(f => g.appendChild(f));
  return { nodes, edges, firstEdge: edges[0] || foldLayer[0] || null };
}

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
