// Layered layout with ELK (Sugiyama): layers in one direction, orthogonal edge routes.
// ELK is a global from its script tag in index.html.

import { CONTAINER_PAD, childrenOf, isAnchor, isContainer, leafSize } from "./index.js";

/**
 * @typedef {import("../../types/nolli").Scene} Scene
 * @typedef {import("../../types/nolli").SceneNode} SceneNode
 * @typedef {import("../../types/nolli").SceneEdge} SceneEdge
 * @typedef {import("../../types/nolli").Laid} Laid
 * @typedef {import("../../types/nolli").Box} Box
 * @typedef {import("../../types/nolli").LayoutOptions} LayoutOptions
 * @typedef {import("elkjs/lib/elk-api").ElkNode} ElkNode
 * @typedef {import("elkjs/lib/elk-api").ElkExtendedEdge & { _e: SceneEdge, container?: string }} ElkEdge
 */

const elk = new ELK();
const P = CONTAINER_PAD;

/** @param {Scene} scene @param {string} direction @returns {ElkNode} */
export function elkGraph(scene, direction) {
  const kidsOf = childrenOf(scene);
  /** @param {SceneNode} n @returns {ElkNode} */
  const build = n => {
    /** @type {Record<string, string>} */
    const opts = {};
    /** @type {ElkNode} */
    const node = { id: n.id, layoutOptions: opts };
    if (isContainer(n)) {
      node.children = (kidsOf.get(n.id) || []).map(build);
      opts["elk.padding"] = `[top=${P.top},left=${P.left},bottom=${P.bottom},right=${P.right}]`;
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
  return {
    id: "ROOT",
    layoutOptions: {
      "elk.algorithm": "layered", "elk.direction": direction, "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.spacing.nodeNode": "22", "elk.layered.spacing.nodeNodeBetweenLayers": "44", "elk.spacing.edgeNode": "12",
      "elk.edgeRouting": "ORTHOGONAL", "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "8", "elk.layered.spacing.edgeNodeBetweenLayers": "14",
      "elk.layered.thoroughness": "10", "elk.layered.cycleBreaking.strategy": "GREEDY",
    },
    children: (kidsOf.get("ROOT") || []).map(build),
    edges,
  };
}

/** @param {Scene} scene @param {LayoutOptions} opts @returns {Promise<Laid>} */
export async function layout(scene, { direction = "DOWN" } = {}) {
  const laid = await elk.layout(elkGraph(scene, direction));
  /** @type {Map<string, Box>} */
  const nodes = new Map();
  /** @param {ElkNode} n @param {number} ox @param {number} oy */
  const place = (n, ox, oy) => {
    const x = ox + (n.x || 0), y = oy + (n.y || 0);
    nodes.set(n.id, { x, y, w: n.width || 0, h: n.height || 0 });
    (n.children || []).forEach(c => place(c, x, y));
  };
  (laid.children || []).forEach(c => place(c, 0, 0));
  // ELK gives edge coordinates relative to the edge's container
  const edges = /** @type {ElkEdge[]} */ (laid.edges || []).map(le => {
    const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
    const ox = c ? c.x : 0, oy = c ? c.y : 0;
    const sections = (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + ox, y: p.y + oy })));
    return { edge: le._e, sections };
  });
  return { width: laid.width || 0, height: laid.height || 0, nodes, edges };
}
