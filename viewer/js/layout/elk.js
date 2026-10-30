// Layered layout with ELK (Sugiyama): layers in one direction, orthogonal edge routes.
// ELK is a global from its script tag in index.html.
//
// Two ways to treat containers:
//   INCLUDE_CHILDREN   one layout problem over every level; edges route straight into
//                      nested boxes, but any change can reorder the whole picture
//   SEPARATE_CHILDREN  each container is laid out on its own, inside out, and is a fixed
//                      box to the level above; only edges between siblings are routed,
//                      edges across levels are drawn straight from border to border. So
//                      that those still set the order (users above what they use), each
//                      becomes an invisible stand-in between the two siblings that contain
//                      its ends, laid out but not drawn

import { CONTAINER_PAD, childrenOf, isAnchor, isContainer, leafSize, straight } from "./index.js";

/**
 * @typedef {import("../../types/nolli").Scene} Scene
 * @typedef {import("../../types/nolli").SceneNode} SceneNode
 * @typedef {import("../../types/nolli").SceneEdge} SceneEdge
 * @typedef {import("../../types/nolli").Laid} Laid
 * @typedef {import("../../types/nolli").Box} Box
 * @typedef {import("../../types/nolli").LayoutOptions} LayoutOptions
 * @typedef {import("elkjs/lib/elk-api").ElkNode} ElkNode
 * @typedef {import("elkjs/lib/elk-api").ElkExtendedEdge & { _e?: SceneEdge, container?: string }} ElkEdge
 * @typedef {"INCLUDE_CHILDREN" | "SEPARATE_CHILDREN"} Hierarchy
 */

const elk = new ELK();
const P = CONTAINER_PAD;

/** @param {string} direction @param {Hierarchy} hierarchy @returns {Record<string, string>} */
const graphOptions = (direction, hierarchy) => ({
  "elk.algorithm": "layered", "elk.direction": direction, "elk.hierarchyHandling": hierarchy,
  "elk.spacing.nodeNode": "22", "elk.layered.spacing.nodeNodeBetweenLayers": "44", "elk.spacing.edgeNode": "12",
  "elk.edgeRouting": "ORTHOGONAL", "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "8", "elk.layered.spacing.edgeNodeBetweenLayers": "14",
  "elk.layered.thoroughness": "10", "elk.layered.cycleBreaking.strategy": "GREEDY",
});

/**
 * @param {Scene} scene @param {string} direction @param {Hierarchy} [hierarchy]
 * @returns {{ graph: ElkNode, unrouted: SceneEdge[] }} unrouted: edges ELK is not given (across levels, separate mode)
 */
export function elkGraph(scene, direction, hierarchy = "INCLUDE_CHILDREN") {
  const separate = hierarchy === "SEPARATE_CHILDREN";
  const kidsOf = childrenOf(scene);
  /** @type {Map<string, ElkNode>} */
  const containers = new Map();
  /** @param {SceneNode} n @returns {ElkNode} */
  const build = n => {
    /** @type {Record<string, string>} */
    const opts = {};
    /** @type {ElkNode} */
    const node = { id: n.id, layoutOptions: opts };
    if (isContainer(n)) {
      // in separate mode each container is its own layout problem and needs all settings
      if (separate) Object.assign(opts, graphOptions(direction, hierarchy));
      containers.set(n.id, node);
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
  /** @type {ElkNode} */
  const graph = { id: "ROOT", layoutOptions: graphOptions(direction, hierarchy), children: (kidsOf.get("ROOT") || []).map(build), edges: [] };
  /** @type {SceneEdge[]} */
  const unrouted = [];
  const parent = new Map(scene.nodes.map(n => [n.id, n.parent]));
  /** the drawn ancestors of id, innermost first, ending with "ROOT" @param {string} id */
  const chain = id => { const out = [id]; let p = parent.get(id); while (p) { out.push(p); p = parent.get(p); } out.push("ROOT"); return out; };
  /** @type {Set<string>} */
  const proxies = new Set();
  scene.edges.forEach((e, i) => {
    /** @type {ElkEdge} */
    const le = { id: "e" + i, sources: [e.src], targets: [e.dst], _e: e };
    if (!separate) { graph.edges?.push(le); return; }
    // separate: an edge between siblings belongs to their container; others are not routed
    const ps = parent.get(e.src), pd = parent.get(e.dst);
    if (ps !== pd) {
      unrouted.push(e);
      // stand-in between the siblings (children of the lowest common container) holding each end
      const cs = chain(e.src), cd = chain(e.dst), common = cs.find(c => cd.includes(c) && c !== e.src && c !== e.dst);
      if (!common) return;
      const a = cs[cs.indexOf(common) - 1], b = cd[cd.indexOf(common) - 1];
      const key = `${a}>${b}`;
      if (!a || !b || a === b || proxies.has(key)) return;
      proxies.add(key);
      const home = common === "ROOT" ? graph : containers.get(common);
      if (home) (home.edges ||= []).push({ id: "p" + proxies.size, sources: [a], targets: [b] });
      return;
    }
    const home = ps ? containers.get(ps) : graph;
    if (!home) { unrouted.push(e); return; }
    (home.edges ||= []).push(le);
  });
  return { graph, unrouted };
}

/** @param {Scene} scene @param {LayoutOptions & { hierarchy?: Hierarchy }} opts @returns {Promise<Laid>} */
export async function layout(scene, { direction = "DOWN", hierarchy = "INCLUDE_CHILDREN" } = {}) {
  const { graph, unrouted } = elkGraph(scene, direction, hierarchy);
  const laid = await elk.layout(graph);
  /** @type {Map<string, Box>} */
  const nodes = new Map();
  /** @param {ElkNode} n @param {number} ox @param {number} oy */
  const place = (n, ox, oy) => {
    const x = ox + (n.x || 0), y = oy + (n.y || 0);
    nodes.set(n.id, { x, y, w: n.width || 0, h: n.height || 0 });
    (n.children || []).forEach(c => place(c, x, y));
  };
  (laid.children || []).forEach(c => place(c, 0, 0));
  /** @type {{ edge: SceneEdge, sections: { x: number, y: number }[][] }[]} */
  const edges = [];
  // ELK gives edge coordinates relative to the edge's container: the node it is declared in
  // (separate) or the one it reports as `container` (include, where all are declared at the root)
  /** @param {ElkNode} n @param {number} ox @param {number} oy */
  const collect = (n, ox, oy) => {
    for (const le of /** @type {ElkEdge[]} */ (n.edges || [])) {
      if (!le._e) continue;  // a stand-in: shapes the layout, not drawn
      const c = le.container && le.container !== "ROOT" && le.container !== n.id ? nodes.get(le.container) : null;
      const x0 = c ? c.x : ox, y0 = c ? c.y : oy;
      const sections = (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + x0, y: p.y + y0 })));
      edges.push({ edge: le._e, sections });
    }
    for (const c of n.children || []) { const b = nodes.get(c.id); if (b && c.children) collect(c, b.x, b.y); }
  };
  collect(laid, 0, 0);
  for (const e of unrouted) {
    const a = nodes.get(e.src), b = nodes.get(e.dst);
    if (a && b) edges.push({ edge: e, sections: [straight(a, b)] });
  }
  // keep the scene's edge order, so drawing order does not depend on where an edge was declared
  const rank = new Map(scene.edges.map((e, i) => [e.key, i]));
  edges.sort((a, b) => (rank.get(a.edge.key) ?? 0) - (rank.get(b.edge.key) ?? 0));
  return { width: laid.width || 0, height: laid.height || 0, nodes, edges };
}
