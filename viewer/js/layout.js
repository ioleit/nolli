// Layout: scene -> positions. No DOM; ELK (a global from its script tag) does the work.
//
// Returns { width, height, nodes, edges }: nodes maps id -> { x, y, w, h } in absolute
// coordinates; edges lists { edge, sections } with each section's points absolute too.
// A different layout or edge routing only has to produce the same shape.

const elk = new ELK();

const textW = (s, icon) => Math.max(56, s.length * 6.4 + 22 + (icon ? 16 : 0));

export function elkGraph(scene, direction) {
  const kidsOf = new Map();
  for (const n of scene.nodes) { const k = n.parent || "ROOT"; if (!kidsOf.has(k)) kidsOf.set(k, []); kidsOf.get(k).push(n); }
  const build = n => {
    const node = { id: n.id, layoutOptions: {} };
    if (n.kind === "anchor") { node.width = 14; node.height = 14; node.layoutOptions["elk.layered.layering.layerConstraint"] = "FIRST"; return node; }
    if (n.container) {
      node.children = (kidsOf.get(n.id) || []).map(build);
      node.layoutOptions["elk.padding"] = "[top=30,left=10,bottom=10,right=10]";
      node.layoutOptions["elk.spacing.nodeNode"] = "16";
      node.layoutOptions["elk.layered.spacing.nodeNodeBetweenLayers"] = "26";
      return node;
    }
    const sub = n.leaves ? "+" + n.leaves : "";
    node.width = Math.max(textW(n.label + sub, n.icon), (n.badge ? n.badge.length * 5.4 : 0) + n.chips.length * 11 + 20);
    node.height = (n.badge || n.chips.length) ? 40 : 30;
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

export async function layout(scene, direction) {
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
