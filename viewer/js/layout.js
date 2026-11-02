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
 * @typedef {import("elkjs/lib/elk-api").ElkExtendedEdge & { _e: SceneEdge | null, container?: string }} ElkEdge
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
// pin: entry nodes pinned to the box's first layer (exit nodes to its last); pull: how much ELK's
// layering keeps a hidden edge short (elk.layered.priority.shortness, network simplex), 0 for
// nothing; debug: the hidden edges and entry nodes come back from layout() to be drawn.
export const LAYOUT = { hierarchy: "INCLUDE_CHILDREN", ports: true, pin: false, pull: 10, debug: false };
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

/** @param {Scene} scene @returns {ElkNode} */
function elkGraph(scene) {
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
    if (isContainer(n)) {
      node.children = (kidsOf.get(n.id) || []).map(build);
      const pad = containerPad(n);
      opts["elk.padding"] = `[top=${pad.top},left=${pad.left},bottom=${pad.bottom},right=${pad.right}]`;
      opts["elk.spacing.nodeNode"] = "16";
      opts["elk.layered.spacing.nodeNodeBetweenLayers"] = "26";
      if (LAYOUT.hierarchy === "SEPARATE_CHILDREN") {  // EXPERIMENT: laid out on its own, so it needs the root's choices
        opts["elk.algorithm"] = "layered"; opts["elk.direction"] = "DOWN"; opts["elk.edgeRouting"] = "ORTHOGONAL";
        opts["elk.layered.nodePlacement.strategy"] = "NETWORK_SIMPLEX"; opts["elk.layered.cycleBreaking.strategy"] = "GREEDY";
      }
      return node;
    }
    const { w, h } = leafSize(n);
    node.width = w; node.height = h;
    if (first.has(n.id)) opts["elk.layered.layering.layerConstraint"] = "FIRST";
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
  /** an entry (in) or exit (out) node inside container c for edge i
   * @param {string} c @param {"in" | "out"} dir @param {number} i */
  const port = (c, dir, i) => {
    const n = built.get(c);
    if (!n) return c;
    const id = `${c}.${dir}${i}`;
    // not pinned to the first or last layer: its edges put it just above (below) what it leads to,
    // where pinning would add a layer of its own at the top (bottom) of the box
    (n.children = n.children || []).push({ id, width: ENTRY, height: ENTRY, layoutOptions: LAYOUT.pin ? { "elk.layered.layering.layerConstraint": dir === "in" ? "FIRST" : "LAST" } : {} });
    return id;
  };
  /** @type {Record<string, string>} */
  const hiddenOpts = LAYOUT.pull > 0 ? { "elk.layered.priority.shortness": String(LAYOUT.pull) } : {};
  scene.edges.forEach((e, i) => {
    let src = e.src, dst = e.dst;
    if (LAYOUT.ports && e.ends) {
      if (containers.has(e.src) && e.ends.src.some(x => x !== e.src)) {
        src = port(e.src, "out", i);
        // not from a box pinned to the last layer: the port's dummy node sits there too, and ELK refuses that
        for (const x of new Set(e.ends.src)) if (built.has(x) && x !== e.src && !last.has(x)) edges.push({ id: `h${i}s${edges.length}`, sources: [x], targets: [src], layoutOptions: hiddenOpts, _e: null });
      }
      if (containers.has(e.dst) && e.ends.dst.some(x => x !== e.dst)) {
        dst = port(e.dst, "in", i);
        for (const x of new Set(e.ends.dst)) if (built.has(x) && x !== e.dst && !first.has(x)) edges.push({ id: `h${i}d${edges.length}`, sources: [dst], targets: [x], layoutOptions: hiddenOpts, _e: null });  // own-code dots are first already
      }
    }
    edges.push({ id: "e" + i, sources: [src], targets: [dst], labels: mark(e, i), _e: e });
  });
  return { id: "ROOT", layoutOptions: { ...GRAPH_OPTIONS, "elk.hierarchyHandling": LAYOUT.hierarchy }, children, edges };
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
  const all = /** @type {ElkEdge[]} */ (laid.edges || []);
  const edges = all.filter(le => le._e).map(le => {
    const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
    const x0 = c ? c.x : 0, y0 = c ? c.y : 0;
    const sections = (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + x0, y: p.y + y0 })));
    const m = (le.labels || [])[0];
    const mark = m && m.x !== undefined && m.y !== undefined ? { x: m.x + x0 + (m.width || 0) / 2, y: m.y + y0 + (m.height || 0) / 2 } : null;
    return { edge: /** @type {SceneEdge} */ (le._e), sections, mark };
  });
  /** @type {Laid["debug"]} what is normally invisible: entry nodes and hidden edges */
  const debug = LAYOUT.debug ? {
    nodes: [...nodes.entries()].filter(([id]) => /\.(in|out)\d+$/.test(id)).map(([, b]) => b),
    edges: all.filter(le => !le._e).map(le => {
      const c = le.container && le.container !== "ROOT" ? nodes.get(le.container) : null;
      const x0 = c ? c.x : 0, y0 = c ? c.y : 0;
      return (le.sections || []).map(s => [s.startPoint, ...(s.bendPoints || []), s.endPoint].map(p => ({ x: p.x + x0, y: p.y + y0 })));
    }),
  } : null;
  return { width: laid.width || 0, height: laid.height || 0, nodes, edges, debug };
}
