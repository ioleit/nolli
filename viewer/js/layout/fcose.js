// Force-directed layout with fCoSE (Cytoscape.js), which keeps a container's children
// together and lays containers out among themselves. Cytoscape runs headless here, only
// to compute positions; edges are straight lines from border to border.

import cytoscape from "https://cdn.jsdelivr.net/npm/cytoscape@3.34.4/+esm";
import fcose from "https://cdn.jsdelivr.net/npm/cytoscape-fcose@2.2.0/+esm";
import { CONTAINER_PAD, childrenOf, leafSize } from "./index.js";

cytoscape.use(fcose);
const P = CONTAINER_PAD;
const MARGIN = 12;

// fCoSE draws on Math.random; a seeded generator keeps the picture deterministic
function seeded(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) { h = Math.imul(h ^ str.charCodeAt(i), 3432918353); h = (h << 13) | (h >>> 19); }
  let a = h >>> 0;
  return () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

export const OPTIONS = {
  name: "fcose", quality: "proof", randomize: true, animate: false, fit: false,
  nodeDimensionsIncludeLabels: false, packComponents: true, tile: true,
  nodeRepulsion: () => 15000, idealEdgeLength: () => 90, edgeElasticity: () => 0.45,
  nestingFactor: 0.1, gravity: 0.25, gravityCompound: 1.0, gravityRangeCompound: 1.5, numIter: 2500,
  tilingPaddingVertical: 12, tilingPaddingHorizontal: 12,
};

// where the segment from a box's centre towards (tx, ty) leaves the box
function exitPoint(b, tx, ty) {
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2, dx = tx - cx, dy = ty - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const s = Math.min(dx ? (b.w / 2) / Math.abs(dx) : Infinity, dy ? (b.h / 2) / Math.abs(dy) : Infinity);
  return { x: cx + dx * s, y: cy + dy * s };
}
const inside = (b, x, y) => x > b.x && x < b.x + b.w && y > b.y && y < b.y + b.h;

export async function layout(scene, { seed = "" } = {}) {
  const sizes = new Map(scene.nodes.filter(n => !n.container).map(n => [n.id, leafSize(n)]));
  const cy = cytoscape({
    headless: true, styleEnabled: true,
    elements: [
      ...scene.nodes.map(n => ({ data: { id: n.id, ...(n.parent ? { parent: n.parent } : {}) } })),
      ...scene.edges.map((e, i) => ({ data: { id: "e" + i, source: e.src, target: e.dst } })),
    ],
    style: [
      { selector: "node", style: { width: n => (sizes.get(n.id()) || { w: 20 }).w, height: n => (sizes.get(n.id()) || { h: 20 }).h, shape: "rectangle" } },
      // uniform padding as large as the header, so our own (header-on-top) box fits inside
      { selector: ":parent", style: { padding: P.top } },
    ],
  });
  const random = Math.random;
  Math.random = seeded(seed + "|" + scene.state.expanded.join(","));
  try { cy.layout(OPTIONS).run(); } finally { Math.random = random; }

  // leaves from cytoscape (centre + size), containers as the union of their children plus padding
  const nodes = new Map(), kids = childrenOf(scene);
  const box = n => {
    if (nodes.has(n.id)) return nodes.get(n.id);
    let b;
    if (n.container) {
      const cs = (kids.get(n.id) || []).map(box);
      const x0 = Math.min(...cs.map(c => c.x)), y0 = Math.min(...cs.map(c => c.y));
      const x1 = Math.max(...cs.map(c => c.x + c.w)), y1 = Math.max(...cs.map(c => c.y + c.h));
      b = { x: x0 - P.left, y: y0 - P.top, w: x1 - x0 + P.left + P.right, h: y1 - y0 + P.top + P.bottom };
    } else {
      const p = cy.getElementById(n.id).position(), s = sizes.get(n.id);
      b = { x: p.x - s.w / 2, y: p.y - s.h / 2, w: s.w, h: s.h };
    }
    nodes.set(n.id, b); return b;
  };
  scene.nodes.forEach(box);
  // shift to the origin
  const all = [...nodes.values()];
  const ox = Math.min(...all.map(b => b.x)) - MARGIN, oy = Math.min(...all.map(b => b.y)) - MARGIN;
  for (const b of all) { b.x -= ox; b.y -= oy; }
  const width = Math.max(...all.map(b => b.x + b.w)) + MARGIN, height = Math.max(...all.map(b => b.y + b.h)) + MARGIN;

  const edges = scene.edges.map(e => {
    const a = nodes.get(e.src), b = nodes.get(e.dst);
    const ac = { x: a.x + a.w / 2, y: a.y + a.h / 2 }, bc = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
    // an edge into the container it sits in ends on that container's header
    const start = inside(a, bc.x, bc.y) ? { x: bc.x, y: a.y } : exitPoint(a, bc.x, bc.y);
    const end = inside(b, ac.x, ac.y) ? { x: ac.x, y: b.y } : exitPoint(b, ac.x, ac.y);
    return { edge: e, sections: [[start, end]] };
  });
  cy.destroy();
  return { width, height, nodes, edges };
}
