// Layout: scene -> positions, with ELK's layered layout (Sugiyama), top to bottom, so
// callers sit above what they call; orthogonal edge routes. No DOM.
//
// ELK is a global from its script tag in index.html. Containers are one layout problem
// over every level (INCLUDE_CHILDREN): edges route straight into nested boxes. The result
// is a Laid (types/nolli.d.ts): absolute boxes per node id and edge routes as point
// lists; draw.js only needs that shape.

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").SceneNode} SceneNode
 * @typedef {import("../types/nolli").SceneEdge} SceneEdge
 * @typedef {import("../types/nolli").Laid} Laid
 * @typedef {import("../types/nolli").Box} Box
 * @typedef {import("elkjs/lib/elk-api").ElkNode} ElkNode
 * @typedef {import("elkjs/lib/elk-api").ElkExtendedEdge & { _e: SceneEdge, container?: string }} ElkEdge
 */

/** @param {SceneNode} n @returns {n is import("../types/nolli").AnchorNode} */
export const isAnchor = n => n.kind === "anchor";

// a box's name is its icons (small tags saying what it is), then a readable label
export const ICON_H = 12, ICON_GAP = 3;
/** @param {string} icon */
export const iconW = icon => Math.max(ICON_H, icon.length * 5.6 + 5);
/** Width the icons take before the label, gap included. @param {string[]} icons */
export const iconsW = icons => icons.reduce((w, i) => w + iconW(i) + ICON_GAP, 0) + (icons.length ? 1 : 0);
/** @param {string} s @param {string[]} icons */
const textW = (s, icons) => Math.max(icons.length && !s ? 20 : 56, s.length * 6.4 + 22 + iconsW(icons));

/** "+12 −3" for a box whose lines changed, "" otherwise. @param {import("../types/nolli").BoxNode} n */
export function statsText(n) {
  const d = n.diff;
  if (!d || (!d.added && !d.removed)) return "";
  return `+${d.added} −${d.removed}`;
}

/** The size a folded node needs for its label, badge, chips and changed lines. @param {SceneNode} n */
function leafSize(n) {
  if (isAnchor(n)) return { w: 14, h: 14 };
  const sub = n.leaves ? "+" + n.leaves : "";
  const stats = statsText(n);
  return {
    w: Math.max(textW(n.label + sub, n.icons), (n.badge ? n.badge.length * 5.4 : 0) + stats.length * 6 + (stats ? 10 : 0) + n.chips.length * 11 + 20),
    h: (n.badge || n.chips.length || stats) ? 40 : 30,
  };
}

/** @param {SceneNode} n */
const isContainer = n => !isAnchor(n) && n.container;

// room around a container's children: the header with title, badges and − sits on top
const CONTAINER_PAD = { top: 30, left: 10, bottom: 10, right: 10 };
export const USES_LINE = 13;  // one line per utility used, bottom right

/** A container's padding: CONTAINER_PAD, plus room at the bottom for its uses list. @param {SceneNode} n */
function containerPad(n) {
  const k = isAnchor(n) ? 0 : (n.uses || []).length;
  return k ? { ...CONTAINER_PAD, bottom: CONTAINER_PAD.bottom + 4 + k * USES_LINE } : CONTAINER_PAD;
}

/** Drawn children per container id ("ROOT" for the top level), in scene order. @param {Scene} scene */
function childrenOf(scene) {
  /** @type {Map<string, SceneNode[]>} */
  const kids = new Map();
  for (const n of scene.nodes) { const k = n.parent || "ROOT"; if (!kids.has(k)) kids.set(k, []); kids.get(k)?.push(n); }
  return kids;
}

const elk = new ELK();

/** @type {Record<string, string>} */
const GRAPH_OPTIONS = {
  "elk.algorithm": "layered", "elk.direction": "DOWN", "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.spacing.nodeNode": "22", "elk.layered.spacing.nodeNodeBetweenLayers": "44", "elk.spacing.edgeNode": "12",
  "elk.edgeRouting": "ORTHOGONAL", "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "8", "elk.layered.spacing.edgeNodeBetweenLayers": "14",
  "elk.layered.thoroughness": "10", "elk.layered.cycleBreaking.strategy": "GREEDY",
};

/** @param {Scene} scene @returns {ElkNode} */
function elkGraph(scene) {
  const kidsOf = childrenOf(scene);
  /** @param {SceneNode} n @returns {ElkNode} */
  const build = n => {
    /** @type {Record<string, string>} */
    const opts = {};
    /** @type {ElkNode} */
    const node = { id: n.id, layoutOptions: opts };
    if (isContainer(n)) {
      node.children = (kidsOf.get(n.id) || []).map(build);
      const pad = containerPad(n);
      opts["elk.padding"] = `[top=${pad.top},left=${pad.left},bottom=${pad.bottom},right=${pad.right}]`;
      opts["elk.spacing.nodeNode"] = "16";
      opts["elk.layered.spacing.nodeNodeBetweenLayers"] = "26";
      return node;
    }
    const { w, h } = leafSize(n);
    node.width = w; node.height = h;
    if (isAnchor(n)) opts["elk.layered.layering.layerConstraint"] = "FIRST";
    else if (n.utility) opts["elk.layered.layering.layerConstraint"] = "LAST";  // utilities sit at the bottom
    return node;
  };
  /** @type {ElkEdge[]} */
  const edges = scene.edges.map((e, i) => ({ id: "e" + i, sources: [e.src], targets: [e.dst], _e: e }));
  return { id: "ROOT", layoutOptions: GRAPH_OPTIONS, children: (kidsOf.get("ROOT") || []).map(build), edges };
}

/** @param {Scene} scene @returns {Promise<Laid>} */
export async function layout(scene) {
  const laid = await elk.layout(elkGraph(scene));
  /** @type {Map<string, Box>} */
  const nodes = new Map();
  /** @param {ElkNode} n @param {number} ox @param {number} oy */
  const place = (n, ox, oy) => {
    const x = ox + (n.x || 0), y = oy + (n.y || 0);
    nodes.set(n.id, { x, y, w: n.width || 0, h: n.height || 0 });
    (n.children || []).forEach(c => place(c, x, y));
  };
  (laid.children || []).forEach(c => place(c, 0, 0));
  // edges are declared at the root; ELK gives their coordinates relative to the container
  // it reports as `container`, and keeps the scene's edge order
  const edges = /** @type {ElkEdge[]} */ (laid.edges || []).map(le => {
    const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
    const x0 = c ? c.x : 0, y0 = c ? c.y : 0;
    const sections = (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + x0, y: p.y + y0 })));
    return { edge: le._e, sections };
  });
  return { width: laid.width || 0, height: laid.height || 0, nodes, edges };
}
