// Drawing: scene + layout -> SVG. Rebuilds everything inside the root group, and hands
// back a handle per drawn node so an animation can move it (update) and fade it.

import { showTip, hideTip } from "./tip.js";
import { isAnchor } from "./layout/index.js";

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
 */

const PALETTE = ["#4a90e2", "#e2874a", "#3a9d5d", "#b05cc9", "#d9534f", "#2aa8a8", "#c9a227", "#7a7a7a"];
/** @param {number} i */
const color = i => PALETTE[(i < 0 ? 7 : i) % PALETTE.length];
const ns = "http://www.w3.org/2000/svg";
/** @param {string} t @param {Record<string, string | number>} [a] @returns {SVGElement} */
const el = (t, a = {}) => { const x = /** @type {SVGElement} */ (document.createElementNS(ns, t)); for (const [k, v] of Object.entries(a)) x.setAttribute(k, String(v)); return x; };
const MARKERS = `<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#999"/></marker><marker id="arr-hollow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="Canvas" stroke="#2aa8a8" stroke-width="1"/></marker>`;
/** @param {Box} b */
const at = b => `translate(${b.x},${b.y})`;

/** @param {SceneEdge} e */
function edgeClass(e) {
  return ["edge", e.local && "local", e.single && "single", e.weak && "weak", e.style, e.dangling && "dangling", e.heuristic && "heuristic"].filter(Boolean).join(" ");
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
  if (live) { grp.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), n.info)); grp.addEventListener("mouseleave", hideTip); }
  grp.appendChild(el("circle", { cx: 7, cy: 7, r: 5, class: "anchor" }));
  return { els: [grp], update: b => grp.setAttribute("transform", at(b)) };
}

/**
 * A box: folded (a leaf) or unfolded (a container whose children are drawn separately).
 * Built at box p; update(b) moves and resizes it, recomputing everything that depends on
 * its size (centred label, badges, the − button).
 * @param {BoxNode} n @param {Box} p @param {((id: string) => void) | null} onToggle null: not interactive
 * @returns {Built}
 */
function buildBox(n, p, onToggle) {
  const isC = n.container;
  const grp = el("g", { class: ["node", isC ? "container" : "folded", n.kind, n.cycle ? "cycle" : ""].join(" "), transform: at(p) });
  if (onToggle) { grp.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), n.info)); grp.addEventListener("mouseleave", hideTip); }
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
  /** the badge text that fits a container w wide @param {number} w */
  const fitted = w => { if (!isC || !n.badge) return n.badge; const room = Math.floor((w - 36 - titleW) / 5.4); return room < 6 ? "" : n.badge.length > room ? n.badge.slice(0, room - 1) + "…" : n.badge; };
  /** @param {number} w */
  const badgeX = w => isC ? w - 28 : (w + n.chips.length * 11) / 2;
  if (fitted(p.w)) { badge = el("text", { class: "badge", x: badgeX(p.w), y: isC ? 18 : 33, "text-anchor": isC ? "end" : "middle" }); badge.textContent = fitted(p.w); if (isC) badge.appendChild(el("title")).textContent = n.badge; grp.appendChild(badge); }
  n.chips.forEach((c, i) => { const ch = el("circle", { cx: 9 + i * 11, cy: isC ? 14 : 32, r: 4, fill: color(c.color), stroke: "Canvas", "stroke-width": 1 }); ch.appendChild(el("title")).textContent = "deployed in " + c.name; grp.appendChild(ch); });
  if (n.service_color !== null && n.service_color !== undefined) rect.setAttribute("stroke", color(n.service_color));
  if (onToggle && !isC && n.foldable) grp.addEventListener("click", ev => { ev.stopPropagation(); onToggle(n.id); });

  /** @param {Box} b */
  const update = b => {
    grp.setAttribute("transform", at(b));
    rect.setAttribute("width", String(b.w)); rect.setAttribute("height", String(b.h));
    if (centred) t.setAttribute("x", String(b.w / 2));
    if (top && foldRect && foldText) { const fx = foldX(b.w); top.setAttribute("transform", at(b)); foldRect.setAttribute("x", String(fx - 9)); foldText.setAttribute("x", String(fx)); }
    if (leaves) leaves.setAttribute("x", String(b.w - 5));
    if (badge) { badge.setAttribute("x", String(badgeX(b.w))); if (isC) badge.firstChild && (badge.firstChild.textContent = fitted(b.w)); }
  };
  return { els: top ? [grp, top] : [grp], update };
}

/** @param {SceneNode} n @param {Box} p @param {((id: string) => void) | null} onToggle */
const build = (n, p, onToggle) => isAnchor(n) ? buildAnchor(n, p, !!onToggle) : buildBox(n, p, onToggle);

/**
 * @param {Element} g the root group
 * @param {Scene} scene
 * @param {Laid} laid
 * @param {(id: string) => void} onToggle unfold or fold a node
 * @returns {Drawn}
 */
export function draw(g, scene, laid, onToggle) {
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
    const { els, update } = build(n, p, onToggle);
    g.appendChild(els[0]);
    if (els[1]) foldLayer.push(els[1]);
    nodes.set(n.id, { update, opacity: fader(els) });
  }

  /** @type {SVGElement[]} */
  const edges = [];
  for (const { edge: e, sections } of laid.edges) {
    const ge = el("g", { class: edgeClass(e), "data-key": e.key });
    /** @type {{ x: number, y: number } | null} */
    let mid = null;
    for (const pts of sections) {
      const d = "M" + pts.map(p => `${p.x},${p.y}`).join("L");
      ge.appendChild(el("path", { d, "marker-end": e.kind === "provide" ? "url(#arr-hollow)" : "url(#arr)", "stroke-width": Math.min(2.6, 0.8 + 0.4 * Math.log2(e.count)) }));
      ge.appendChild(el("path", { d, style: "stroke:transparent;stroke-width:9px;fill:none" }));
      const a = pts[Math.floor((pts.length - 1) / 2)], b = pts[Math.ceil((pts.length - 1) / 2)]; mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }
    if (e.label && mid && !e.weak && !e.local) { const t = el("text", { x: mid.x + 3, y: mid.y - 3 }); t.textContent = e.label; ge.appendChild(t); }
    ge.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), e.info));
    ge.addEventListener("mouseleave", hideTip);
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
