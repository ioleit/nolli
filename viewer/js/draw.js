// Drawing: scene + layout -> SVG. Rebuilds everything inside the root group.

import { showTip, hideTip } from "./tip.js";
import { isAnchor } from "./layout/index.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").SceneEdge} SceneEdge
 * @typedef {import("../types/nolli").BoxNode} BoxNode
 * @typedef {import("../types/nolli").Laid} Laid
 * @typedef {import("../types/nolli").Box} Box
 */

const PALETTE = ["#4a90e2", "#e2874a", "#3a9d5d", "#b05cc9", "#d9534f", "#2aa8a8", "#c9a227", "#7a7a7a"];
/** @param {number} i */
const color = i => PALETTE[(i < 0 ? 7 : i) % PALETTE.length];
const ns = "http://www.w3.org/2000/svg";
/** @param {string} t @param {Record<string, string | number>} [a] @returns {SVGElement} */
const el = (t, a = {}) => { const x = /** @type {SVGElement} */ (document.createElementNS(ns, t)); for (const [k, v] of Object.entries(a)) x.setAttribute(k, String(v)); return x; };
const MARKERS = `<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#999"/></marker><marker id="arr-hollow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="Canvas" stroke="#2aa8a8" stroke-width="1"/></marker>`;

/** @param {SceneEdge} e */
function edgeClass(e) {
  return ["edge", e.local && "local", e.single && "single", e.weak && "weak", e.style, e.dangling && "dangling", e.heuristic && "heuristic"].filter(Boolean).join(" ");
}

/**
 * @param {Element} g the root group
 * @param {Scene} scene
 * @param {Laid} laid
 * @param {(id: string) => void} onToggle unfold or fold a node
 */
export function draw(g, scene, laid, onToggle) {
  g.innerHTML = "";
  const defs = el("defs"); defs.innerHTML = MARKERS; g.appendChild(defs);
  /** @type {SVGElement[]} */
  const foldLayer = [];

  /** @param {BoxNode} n @param {Box} p */
  const drawBox = (n, p) => {
    const isC = n.container;
    const grp = el("g", { class: ["node", isC ? "container" : "folded", n.kind, n.cycle ? "cycle" : ""].join(" "), transform: `translate(${p.x},${p.y})` });
    grp.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), n.tip));
    grp.addEventListener("mouseleave", hideTip);
    grp.appendChild(el("rect", { width: p.w, height: p.h }));
    const icon = n.icon;
    const tx = isC ? (icon ? 24 : 8) : (icon ? 22 : p.w / 2);
    if (icon) { const ig = el("g", { class: "icon " + icon, transform: `translate(${isC ? 7 : 6}, ${isC ? 7 : 9})` }); ig.appendChild(el("rect", { width: 12, height: 12, rx: 3 })); const it = el("text", { x: 6, y: 9.5, "text-anchor": "middle" }); it.textContent = icon; ig.appendChild(it); grp.appendChild(ig); }
    const t = el("text", { x: tx, y: isC ? 18 : 19, "text-anchor": isC || icon ? "start" : "middle", "font-weight": isC ? "600" : "400" });
    t.textContent = n.label; grp.appendChild(t);
    // container header: title, then badges right-aligned (truncated to the room left), then the
    // fold button; a container narrower than its title gets the button right after the title
    const titleW = n.label.length * 7.2 + 8 + (icon ? 16 : 0);
    const narrow = isC && p.w < titleW + 40;
    if (isC) {
      // only the − folds a container; clicks elsewhere on it must not interfere with navigation
      // a group with a transparent hit area: node text has pointer-events off, and the glyph alone is a tiny target
      const fx = narrow ? titleW + 10 : p.w - 14;
      const f = el("g", { class: "fold" });
      f.appendChild(el("rect", { x: fx - 9, y: 5, width: 18, height: 18, rx: 3 }));
      const ft = el("text", { x: fx, y: 18, "text-anchor": "middle" }); ft.textContent = "−"; f.appendChild(ft);
      f.appendChild(el("title")).textContent = "fold";
      f.addEventListener("mousedown", ev => ev.stopPropagation());  // don't start a pan
      f.addEventListener("click", ev => { ev.stopPropagation(); onToggle(n.id); });
      // drawn above the edges (an edge's hover strip would otherwise swallow the click)
      const top = el("g", { class: "node", transform: `translate(${p.x},${p.y})` }); top.appendChild(f); foldLayer.push(top);
    }
    if (n.utility) grp.classList.add("utility");
    if (!isC && n.leaves) { const b = el("text", { class: "badge", x: p.w - 5, y: 10, "text-anchor": "end" }); b.textContent = "+" + n.leaves; grp.appendChild(b); }
    let badge = n.badge;
    if (isC && badge) { const room = Math.floor((p.w - 36 - titleW) / 5.4); badge = room < 6 ? "" : badge.length > room ? badge.slice(0, room - 1) + "…" : badge; }
    if (badge) { const b = el("text", { class: "badge", x: isC ? p.w - 28 : (p.w + n.chips.length * 11) / 2, y: isC ? 18 : 33, "text-anchor": isC ? "end" : "middle" }); b.textContent = badge; if (isC) b.appendChild(el("title")).textContent = n.badge; grp.appendChild(b); }
    n.chips.forEach((c, i) => { const ch = el("circle", { cx: 9 + i * 11, cy: isC ? 14 : 32, r: 4, fill: color(c.color), stroke: "Canvas", "stroke-width": 1 }); ch.appendChild(el("title")).textContent = "deployed in " + c.name; grp.appendChild(ch); });
    if (n.service_color !== null && n.service_color !== undefined) grp.querySelector("rect")?.setAttribute("stroke", color(n.service_color));
    if (!isC && n.foldable) grp.addEventListener("click", ev => { ev.stopPropagation(); onToggle(n.id); });
    g.appendChild(grp);
  };

  // scene nodes come in drawing order: a container before what it contains
  for (const n of scene.nodes) {
    const p = laid.nodes.get(n.id);
    if (!p) continue;
    if (isAnchor(n)) {
      // the module's own code (functions, constants, wiring): a dot that edges attach to
      const grp = el("g", { class: "node folded files ", transform: `translate(${p.x},${p.y})` });
      grp.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), n.tip));
      grp.addEventListener("mouseleave", hideTip);
      grp.appendChild(el("circle", { cx: 7, cy: 7, r: 5, class: "anchor" }));
      g.appendChild(grp);
    } else drawBox(n, p);
  }

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
    ge.addEventListener("mousemove", ev => showTip(/** @type {MouseEvent} */ (ev), e.tip));
    ge.addEventListener("mouseleave", hideTip);
    g.appendChild(ge);
  }
  foldLayer.forEach(f => g.appendChild(f));
}
