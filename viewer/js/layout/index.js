// Layout: scene -> positions, by one of several engines. No DOM.
//
// Every engine returns a Laid (types/nolli.d.ts): absolute boxes per node id and edge
// routes as point lists. draw.js only needs that shape.

/**
 * @typedef {import("../../types/nolli").Scene} Scene
 * @typedef {import("../../types/nolli").SceneNode} SceneNode
 * @typedef {import("../../types/nolli").Laid} Laid
 * @typedef {import("../../types/nolli").LayoutOptions} LayoutOptions
 * @typedef {{ layout: (scene: Scene, opts: LayoutOptions) => Promise<Laid> }} Engine
 */

/** @type {Record<string, { label: string, load: () => Promise<Engine> }>} */
export const ENGINES = {
  elk: { label: "layered (ELK)", load: () => import("./elk.js") },
  elksep: { label: "layered, each box separately (ELK)", load: async () => {
    const elk = await import("./elk.js");
    return { layout: (scene, opts) => elk.layout(scene, { ...opts, hierarchy: "SEPARATE_CHILDREN" }) };
  } },
  fcose: { label: "force (fCoSE)", load: () => import("./fcose.js") },
};

/** @param {Scene} scene @param {LayoutOptions} opts @returns {Promise<Laid>} */
export async function layout(scene, opts) {
  const engine = ENGINES[opts.engine || "elk"] || ENGINES.elk;
  return (await engine.load()).layout(scene, opts);
}

// ---------- shared by engines ----------

/** @param {SceneNode} n @returns {n is import("../../types/nolli").AnchorNode} */
export const isAnchor = n => n.kind === "anchor";

/** @param {string} s @param {string} icon */
const textW = (s, icon) => Math.max(56, s.length * 6.4 + 22 + (icon ? 16 : 0));

/** The size a folded node needs for its label, badge and chips. @param {SceneNode} n */
export function leafSize(n) {
  if (isAnchor(n)) return { w: 14, h: 14 };
  const sub = n.leaves ? "+" + n.leaves : "";
  return {
    w: Math.max(textW(n.label + sub, n.icon), (n.badge ? n.badge.length * 5.4 : 0) + n.chips.length * 11 + 20),
    h: (n.badge || n.chips.length) ? 40 : 30,
  };
}

/** @param {SceneNode} n */
export const isContainer = n => !isAnchor(n) && n.container;

// room around a container's children: the header with title, badges and − sits on top
export const CONTAINER_PAD = { top: 30, left: 10, bottom: 10, right: 10 };
export const USES_LINE = 13;  // one line per utility used, bottom right

/** A container's padding: CONTAINER_PAD, plus room at the bottom for its uses list. @param {SceneNode} n */
export function containerPad(n) {
  const k = isAnchor(n) ? 0 : (n.uses || []).length;
  return k ? { ...CONTAINER_PAD, bottom: CONTAINER_PAD.bottom + 4 + k * USES_LINE } : CONTAINER_PAD;
}

/** Where the segment from a box's centre towards (tx, ty) leaves the box. @param {import("../../types/nolli").Box} b @param {number} tx @param {number} ty @returns {import("../../types/nolli").Point} */
function exitPoint(b, tx, ty) {
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2, dx = tx - cx, dy = ty - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const s = Math.min(dx ? (b.w / 2) / Math.abs(dx) : Infinity, dy ? (b.h / 2) / Math.abs(dy) : Infinity);
  return { x: cx + dx * s, y: cy + dy * s };
}
/** @param {import("../../types/nolli").Box} b @param {number} x @param {number} y */
const inside = (b, x, y) => x > b.x && x < b.x + b.w && y > b.y && y < b.y + b.h;

/**
 * A straight edge from border to border, along the line between the boxes' centres; an
 * edge into the container it sits in ends on that container's header.
 * @param {import("../../types/nolli").Box} a @param {import("../../types/nolli").Box} b
 */
export function straight(a, b) {
  const ac = { x: a.x + a.w / 2, y: a.y + a.h / 2 }, bc = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
  const start = inside(a, bc.x, bc.y) ? { x: bc.x, y: a.y } : exitPoint(a, bc.x, bc.y);
  const end = inside(b, ac.x, ac.y) ? { x: ac.x, y: b.y } : exitPoint(b, ac.x, ac.y);
  return [start, end];
}

/** Drawn children per container id ("ROOT" for the top level), in scene order. @param {Scene} scene */
export function childrenOf(scene) {
  /** @type {Map<string, SceneNode[]>} */
  const kids = new Map();
  for (const n of scene.nodes) { const k = n.parent || "ROOT"; if (!kids.has(k)) kids.set(k, []); kids.get(k)?.push(n); }
  return kids;
}
