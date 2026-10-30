// Layered layout with ELK (Sugiyama): layers in one direction, orthogonal edge routes.
// ELK is a global from its script tag in index.html.

import { CONTAINER_PAD, childrenOf, leafSize } from "./index.js";

const elk = new ELK();
const P = CONTAINER_PAD;

export function elkGraph(scene, direction) {
  const kidsOf = childrenOf(scene);
  const build = n => {
    const node = { id: n.id, layoutOptions: {} };
    if (n.container) {
      node.children = (kidsOf.get(n.id) || []).map(build);
      node.layoutOptions["elk.padding"] = `[top=${P.top},left=${P.left},bottom=${P.bottom},right=${P.right}]`;
      node.layoutOptions["elk.spacing.nodeNode"] = "16";
      node.layoutOptions["elk.layered.spacing.nodeNodeBetweenLayers"] = "26";
      return node;
    }
    const { w, h } = leafSize(n);
    node.width = w; node.height = h;
    if (n.kind === "anchor") node.layoutOptions["elk.layered.layering.layerConstraint"] = "FIRST";
    if (n.utility) node.layoutOptions["elk.layered.layering.layerConstraint"] = "LAST";  // utilities sit at the bottom
    return node;
  };
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
    edges: scene.edges.map((e, i) => ({ id: "e" + i, sources: [e.src], targets: [e.dst], _e: e })),
  };
}

export async function layout(scene, { direction = "DOWN" } = {}) {
  const laid = await elk.layout(elkGraph(scene, direction));
  const nodes = new Map();
  const place = (n, ox, oy) => { const x = ox + (n.x || 0), y = oy + (n.y || 0); nodes.set(n.id, { x, y, w: n.width, h: n.height }); (n.children || []).forEach(c => place(c, x, y)); };
  laid.children.forEach(c => place(c, 0, 0));
  // ELK gives edge coordinates relative to the edge's container
  const edges = (laid.edges || []).map(le => {
    const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
    const ox = c ? c.x : 0, oy = c ? c.y : 0;
    const sections = (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + ox, y: p.y + oy })));
    return { edge: le._e, sections };
  });
  return { width: laid.width, height: laid.height, nodes, edges };
}
