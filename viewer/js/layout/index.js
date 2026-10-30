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

/** Drawn children per container id ("ROOT" for the top level), in scene order. @param {Scene} scene */
export function childrenOf(scene) {
  /** @type {Map<string, SceneNode[]>} */
  const kids = new Map();
  for (const n of scene.nodes) { const k = n.parent || "ROOT"; if (!kids.has(k)) kids.set(k, []); kids.get(k)?.push(n); }
  return kids;
}
