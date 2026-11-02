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
 * @typedef {import("../types/nolli").Point} Point
 * @typedef {import("elkjs/lib/elk-api").ElkNode} ElkNode
 * @typedef {import("elkjs/lib/elk-api").ElkExtendedEdge & { _e: SceneEdge | null, _hidden?: "pull" | "anchor", container?: string }} ElkEdge
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
export const HEADER_H = 26;  // the header's hover area (draw.js), above where children start
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

// EXPERIMENT (not for merging): ELK's hierarchy handling, set from the sidebar (main.js).
// INCLUDE_CHILDREN lays the whole hierarchy out at once (edges across levels take part);
// SEPARATE_CHILDREN lays each unfolded box out on its own, then places the boxes.
// pin: anchor edges keep the entry node at the top of its box and the exit node at the bottom
// (see elkGraph); pull: parallel copies of each hidden edge, its weight in the layering, 1..20;
// debug: the hidden edges and entry nodes come back from layout() to be drawn.
export const LAYOUT = { hierarchy: "INCLUDE_CHILDREN", ports: true, pin: true, pull: 1, debug: false, twoPass: true };
// twoPass: lay out once with the hidden edges, then again without them, keeping the layers of
// the first pass (ELK's interactive layering from the first pass's positions), so the hidden
// edges shape the layering but take no routing space in the drawing.
// ports: a lifted line (scene edge with `ends`, rollup.lift) ends at an entry node inside the
// box it was lifted to, a tiny invisible node in the box's first layer (an exit node in its
// last layer for lines leaving), and hidden edges run from there to the drawn nodes the line
// stands for, so ELK still pulls what outside code depends on to the top of the box (and
// what depends on the outside to the bottom) although only the one line is drawn. ELK's own
// ports on a compound node do the same but crash elkjs 0.9.3 on nested boxes with edges
// across levels (checked 2026-11-02), hence nodes.
export const ENTRY = 2;  // an entry or exit node's size

/** @type {Record<string, string>} */
const GRAPH_OPTIONS = {
  "elk.algorithm": "layered", "elk.direction": "DOWN", "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.spacing.nodeNode": "22", "elk.layered.spacing.nodeNodeBetweenLayers": "44", "elk.spacing.edgeNode": "12",
  "elk.edgeRouting": "ORTHOGONAL", "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "8", "elk.layered.spacing.edgeNodeBetweenLayers": "14",
  "elk.layered.thoroughness": "10", "elk.layered.cycleBreaking.strategy": "GREEDY",
};

/** A verdict mark's size: the disc draw.js draws, r = 7, and a pixel around it. */
const MARK = 16;

/** @param {Scene} scene @param {Map<string, {x: number, y: number}> | null} [positions] from a first pass:
 * the second pass keeps its layers (interactive layering) and adds no hidden edges
 * @returns {{ graph: ElkNode, hidden: { kind: "pull" | "anchor", from: string, to: string }[] }} */
function elkGraph(scene, positions = null) {
  /** @type {{ kind: "pull" | "anchor", from: string, to: string }[]} every hidden edge once, for the debug view */
  const hidden = [];
  const kidsOf = childrenOf(scene);
  // anchors go to the first layer and utilities to the last, but ELK refuses an edge between
  // two boxes pinned to the same end (a review keeps a changed edge between utilities): those
  // boxes are left free
  const first = new Set(scene.nodes.filter(isAnchor).map(n => n.id)), last = new Set(scene.nodes.filter(n => !isAnchor(n) && n.utility).map(n => n.id));
  for (const e of scene.edges) for (const pinned of [first, last]) if (pinned.has(e.src) && pinned.has(e.dst)) { pinned.delete(e.src); pinned.delete(e.dst); }
  /** @param {SceneNode} n @returns {ElkNode} */
  const build = n => {
    /** @type {Record<string, string>} */
    const opts = {};
    /** @type {ElkNode} */
    const node = { id: n.id, layoutOptions: opts };
    const at = positions && positions.get(n.id);
    if (at) { node.x = at.x; node.y = at.y; }
    if (isContainer(n)) {
      node.children = (kidsOf.get(n.id) || []).map(build);
      const pad = containerPad(n);
      opts["elk.padding"] = `[top=${pad.top},left=${pad.left},bottom=${pad.bottom},right=${pad.right}]`;
      opts["elk.spacing.nodeNode"] = "16";
      opts["elk.layered.spacing.nodeNodeBetweenLayers"] = "26";
      if (positions) { opts["elk.layered.layering.strategy"] = "INTERACTIVE"; opts["elk.interactive"] = "true"; }  // each box keeps its layers too
      if (LAYOUT.hierarchy === "SEPARATE_CHILDREN") {  // EXPERIMENT: laid out on its own, so it needs the root's choices
        opts["elk.algorithm"] = "layered"; opts["elk.direction"] = "DOWN"; opts["elk.edgeRouting"] = "ORTHOGONAL";
        opts["elk.layered.nodePlacement.strategy"] = "NETWORK_SIMPLEX"; opts["elk.layered.cycleBreaking.strategy"] = "GREEDY";
      }
      return node;
    }
    const { w, h } = leafSize(n);
    node.width = w; node.height = h;
    if (positions) { /* the first pass's positions already have them first or last */ }
    else if (first.has(n.id)) opts["elk.layered.layering.layerConstraint"] = "FIRST";
    else if (last.has(n.id)) opts["elk.layered.layering.layerConstraint"] = "LAST";  // utilities sit at the bottom
    return node;
  };
  // an edge's verdict mark is its label, inline: ELK makes room for it halfway along and routes
  // the edge through its middle (a label without text is left unplaced, so the verdict is its text)
  const mark = /** @param {SceneEdge} e @param {number} i */ (e, i) => e.verdict
    ? [{ id: "m" + i, text: e.verdict, width: MARK, height: MARK, layoutOptions: { "elk.edgeLabels.inline": "true" } }] : [];
  const children = (kidsOf.get("ROOT") || []).map(build);
  /** @type {Map<string, ElkNode>} every built node by id */
  const built = new Map();
  const index = /** @param {ElkNode} n */ n => { built.set(n.id, n); (n.children || []).forEach(index); };
  children.forEach(index);
  const containers = new Set(scene.nodes.filter(isContainer).map(n => n.id));
  /** @type {ElkEdge[]} */
  const edges = [];
  // How the hidden edges work in elkjs 0.9.3 (probed 2026-11-02): a box is layered on its own,
  // edges to the outside have no pull inside it; a node pinned FIRST/LAST is taken out of the
  // layering and put back afterwards, so its edges count for nothing; elk.layered.priority.shortness
  // acts the wrong way round; parallel edges act as weight. Hence: one entry and one exit node per
  // box, free; with `pin`, anchor edges from the entry node to every child and from every child to
  // the exit node keep them at the top and bottom inside the layering (at the cost of a layer of
  // their own); `pull` parallel copies of each hidden edge are its weight.
  /** @type {Map<string, string>} the entry (c.in) or exit (c.out) node of a container, made once */
  const gates = new Map();
  /** @param {string} c @param {"in" | "out"} dir */
  const gate = (c, dir) => {
    const id = `${c}.${dir}`;
    if (gates.has(id)) return id;
    const n = built.get(c);
    if (!n) return c;
    const at = positions && positions.get(id);
    (n.children = n.children || []).push({ id, width: ENTRY, height: ENTRY, layoutOptions: {}, ...(at ? { x: at.x, y: at.y } : {}) });
    gates.set(id, c);
    if (LAYOUT.pin) for (const kid of kidsOf.get(c) || []) {
      if (dir === "in" && !first.has(kid.id)) hiddenEdge("anchor", id, kid.id);
      if (dir === "out" && !last.has(kid.id)) hiddenEdge("anchor", kid.id, id);
    }
    return id;
  };
  const copies = Math.max(1, Math.min(20, Math.round(LAYOUT.pull)));
  /** a hidden edge: in a second pass only remembered, the layers already have it
   * @param {"pull" | "anchor"} kind @param {string} from @param {string} to */
  const hiddenEdge = (kind, from, to) => {
    hidden.push({ kind, from, to });
    if (positions) return;
    for (let k = 0; k < (kind === "pull" ? copies : 1); k++) edges.push({ id: `h${edges.length}`, sources: [from], targets: [to], _e: null, _hidden: kind });
  };
  /** @param {string} from @param {string} to */
  const pull = (from, to) => hiddenEdge("pull", from, to);
  // the drawn line ends at the box's boundary either way (owner's choice, 2026-11-02): the entry
  // and exit nodes only carry the hidden edges that shape the layering
  scene.edges.forEach((e, i) => {
    if (LAYOUT.ports && e.ends) {
      if (containers.has(e.src) && e.ends.src.some(x => x !== e.src)) {
        const out = gate(e.src, "out");
        // not from a box pinned to the last layer (a utility): it is taken out of the layering anyway
        for (const x of new Set(e.ends.src)) if (built.has(x) && x !== e.src && !last.has(x)) pull(x, out);
      }
      if (containers.has(e.dst) && e.ends.dst.some(x => x !== e.dst)) {
        const entry = gate(e.dst, "in");
        for (const x of new Set(e.ends.dst)) if (built.has(x) && x !== e.dst && !first.has(x)) pull(entry, x);  // own-code dots are first already
      }
    }
    edges.push({ id: "e" + i, sources: [e.src], targets: [e.dst], labels: mark(e, i), _e: e });
  });
  // parallel hidden edges are routed as one (they would take channel space for every copy)
  /** @type {Record<string, string>} */
  const merge = LAYOUT.ports && copies > 1 && !positions ? { "elk.layered.mergeEdges": "true" } : {};
  /** @type {Record<string, string>} interactive crossing minimisation is not hierarchy-aware, so only the layering keeps */
  const keep = positions ? { "elk.layered.layering.strategy": "INTERACTIVE", "elk.interactive": "true" } : {};
  return { graph: { id: "ROOT", layoutOptions: { ...GRAPH_OPTIONS, "elk.hierarchyHandling": LAYOUT.hierarchy, ...merge, ...keep }, children, edges }, hidden };
}

/** @param {Scene} scene @returns {Promise<Laid>} */
export async function layout(scene) {
  let { graph, hidden } = elkGraph(scene);
  let laid = await elk.layout(graph);
  if (LAYOUT.ports && LAYOUT.twoPass) {
    // the first pass's positions (relative to the parent, as ELK's interactive layering reads them)
    /** @type {Map<string, {x: number, y: number}>} */
    const positions = new Map();
    const walk = /** @param {ElkNode} n */ n => { for (const c of n.children || []) { positions.set(c.id, { x: c.x || 0, y: c.y || 0 }); walk(c); } };
    walk(laid);
    ({ graph, hidden } = elkGraph(scene, positions));
    laid = await elk.layout(graph);
  }
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
  const all = /** @type {ElkEdge[]} */ (laid.edges || []);
  const edges = all.filter(le => le._e).map(le => {
    const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
    const x0 = c ? c.x : 0, y0 = c ? c.y : 0;
    const sections = (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + x0, y: p.y + y0 })));
    const m = (le.labels || [])[0];
    const mark = m && m.x !== undefined && m.y !== undefined ? { x: m.x + x0 + (m.width || 0) / 2, y: m.y + y0 + (m.height || 0) / 2 } : null;
    return { edge: /** @type {SceneEdge} */ (le._e), sections, mark };
  });
  /** @type {Laid["debug"]} what is normally invisible: entry nodes and hidden edges (as ELK routed
   * them, or, after a second pass that had none, straight between the boxes they connect) */
  /** @param {string} id @returns {Point | null} */
  function centre(id) { const b = nodes.get(id); return b ? { x: b.x + b.w / 2, y: b.y + b.h / 2 } : null; }
  const routed = all.filter(le => !le._e);
  const debug = LAYOUT.debug ? {
    nodes: [...nodes.entries()].filter(([id]) => /\.(in|out)$/.test(id)).map(([, b]) => b),
    edges: routed.length ? routed.map(le => {
      const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
      const x0 = c ? c.x : 0, y0 = c ? c.y : 0;
      return { kind: le._hidden || "pull", sections: (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + x0, y: p.y + y0 }))) };
    }) : hidden.map(h => { const a = centre(h.from), b = centre(h.to); return { kind: h.kind, sections: a && b ? [[a, b]] : [] }; }),
  } : null;
  return { width: laid.width || 0, height: laid.height || 0, nodes, edges, debug };
}
