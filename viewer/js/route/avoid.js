// Edge routing around boxes with libavoid (libavoid-js, LGPL, loaded unmodified from a CDN).
//
// libavoid ignores an obstacle that has other obstacles inside it, so containers are not
// handed to it as one big problem. Routing goes level by level, like the separate layout:
// every container (and the top level) is its own routing problem whose obstacles are its
// direct children, nested containers being solid boxes there. An edge from deep inside
// container A to deep inside container B is routed in pieces: inside A from its start to
// a port on A's border, at the level where A's and B's ancestors are siblings from port
// to port, and inside B from its port to the end. Ports sit on the side of a container
// that faces the other end. The pieces are joined into one orthogonal line.

/**
 * @typedef {import("../../types/nolli").Scene} Scene
 * @typedef {import("../../types/nolli").SceneEdge} SceneEdge
 * @typedef {import("../../types/nolli").Laid} Laid
 * @typedef {import("../../types/nolli").Box} Box
 * @typedef {import("../../types/nolli").Point} Point
 * @typedef {{ level: string, from: Point, to: Point }} Piece  one routing problem's share of an edge
 * @typedef {any} Avoid  the libavoid-js instance (embind; typed loosely)
 */

const ROOT = "ROOT";
const PORT_INSET = 8;  // keep ports this far from a container's corners

/** @param {Box} b */
const centre = b => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
/** @param {number} v @param {number} lo @param {number} hi */
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * The point on box c's border where an edge towards (or from) `ref` crosses it: on the
 * side facing ref, as close to ref as that side allows.
 * @param {Box} c @param {Point} ref @returns {Point}
 */
export function portOn(c, ref) {
  const ix = Math.min(PORT_INSET, c.w / 2), iy = Math.min(PORT_INSET, c.h / 2);
  const x = clamp(ref.x, c.x + ix, c.x + c.w - ix), y = clamp(ref.y, c.y + iy, c.y + c.h - iy);
  if (ref.y >= c.y + c.h) return { x, y: c.y + c.h };
  if (ref.y <= c.y) return { x, y: c.y };
  return ref.x >= c.x + c.w ? { x: c.x + c.w, y } : { x: c.x, y };
}

/**
 * Split each edge into pieces, one per routing level it passes through.
 * @param {Scene} scene @param {Laid} laid
 * @returns {Map<string, Piece[]>} edge key -> pieces in order from source to target
 */
export function pieces(scene, laid) {
  const parent = new Map(scene.nodes.map(n => [n.id, n.parent]));
  const container = new Set(scene.nodes.filter(n => "container" in n && n.container).map(n => n.id));
  /** @param {string} id */
  const chain = id => { const out = [id]; let p = parent.get(id); while (p) { out.push(p); p = parent.get(p); } out.push(ROOT); return out; };
  /** @param {string} id */
  const box = id => /** @type {Box} */ (laid.nodes.get(id));
  /** @type {Map<string, Piece[]>} */
  const out = new Map();
  for (const e of scene.edges) {
    if (!laid.nodes.has(e.src) || !laid.nodes.has(e.dst)) continue;
    const cs = chain(e.src), cd = chain(e.dst);
    const common = /** @type {string} */ (cs.find(c => cd.includes(c)));
    // up: src and the containers above it below the common level; down: the same for dst
    const up = cs.slice(0, cs.indexOf(common)), down = cd.slice(0, cd.indexOf(common));
    // the far ends, as seen from the common level
    const refUp = down.length ? centre(box(down[down.length - 1])) : centre(box(common));
    const refDown = up.length ? centre(box(up[up.length - 1])) : centre(box(common));
    /** @type {Piece[]} */
    const ps = [];
    // start: a leaf's centre (clipped to its border later); a container's port
    let at = up.length && container.has(up[0]) ? portOn(box(up[0]), refUp) : up.length ? centre(box(up[0])) : portOn(box(common), refDown);
    for (let i = 1; i < up.length; i++) {
      const port = portOn(box(up[i]), refUp);
      ps.push({ level: up[i], from: at, to: port });
      at = port;
    }
    // across the common level, to the down side's outermost port (or the end itself)
    const ends = down.map((id, i) => i === 0 && !container.has(id) ? centre(box(id)) : portOn(box(id), refDown));
    // into the container it sits in: up to that container's header
    if (!down.length) { const c = box(common); ends.push({ x: clamp(at.x, c.x + PORT_INSET, c.x + c.w - PORT_INSET), y: c.y }); }
    ps.push({ level: common, from: at, to: ends[ends.length - 1] });
    for (let i = down.length - 1; i >= 1; i--) ps.push({ level: down[i], from: ends[i], to: ends[i - 1] });
    out.set(e.key, ps.filter(p => p.from.x !== p.to.x || p.from.y !== p.to.y));
  }
  return out;
}

/** Points from inside box b up to where the line leaves it, replaced by that border point. @param {Point[]} pts @param {Box} b */
function clipStart(pts, b) {
  const inside = (/** @type {Point} */ p) => p.x > b.x + 0.5 && p.x < b.x + b.w - 0.5 && p.y > b.y + 0.5 && p.y < b.y + b.h - 0.5;
  let i = 0;
  while (i < pts.length - 1 && inside(pts[i + 1])) i++;
  if (!inside(pts[i])) return pts;
  const a = pts[i], c = pts[i + 1];
  // orthogonal segment: leave through the side it heads to (coordinates from libavoid are
  // doubles, so "vertical" is decided with a tolerance, not by equality)
  const p = Math.abs(a.x - c.x) <= 0.5 ? { x: c.x, y: c.y > a.y ? b.y + b.h : b.y } : { x: c.x > a.x ? b.x + b.w : b.x, y: c.y };
  return [p, ...pts.slice(i + 1)];
}

/**
 * Make every segment horizontal or vertical: libavoid connects an end that lies inside a
 * box's buffer zone (a port on a container's border) with a slanted segment. Each slanted
 * segment gets an elbow, turning the way the line was already going.
 * @param {Point[]} pts
 */
function orthogonal(pts) {
  /** @type {Point[]} */
  const out = [];
  for (const p of pts) {
    const a = out[out.length - 1];
    if (a && Math.abs(a.x - p.x) > 0.5 && Math.abs(a.y - p.y) > 0.5) {
      const before = out[out.length - 2];
      const vertical = before ? Math.abs(before.x - a.x) <= 0.5 : true;  // first move: vertical, as layers run top-down
      out.push(vertical ? { x: a.x, y: p.y } : { x: p.x, y: a.y });
    }
    out.push(p);
  }
  return out;
}

/** Drop repeated points and the middle of straight runs. @param {Point[]} pts */
function simplify(pts) {
  /** @type {Point[]} */
  const out = [];
  for (const p of pts) {
    const q = out[out.length - 1];
    if (q && Math.abs(q.x - p.x) < 0.01 && Math.abs(q.y - p.y) < 0.01) continue;
    const r = out[out.length - 2];
    if (q && r && ((Math.abs(r.x - q.x) < 0.01 && Math.abs(q.x - p.x) < 0.01) || (Math.abs(r.y - q.y) < 0.01 && Math.abs(q.y - p.y) < 0.01))) out.pop();
    out.push({ x: p.x, y: p.y });
  }
  return out;
}

/**
 * Route every edge of a laid-out scene around the boxes; returns the layout with new edges.
 * @param {Avoid} A a loaded libavoid-js instance
 * @param {Scene} scene @param {Laid} laid
 * @returns {Laid}
 */
export function routeWith(A, scene, laid) {
  const split = pieces(scene, laid);
  const kids = new Map();
  for (const n of scene.nodes) { const k = n.parent || ROOT; if (!kids.has(k)) kids.set(k, []); kids.get(k).push(n.id); }
  /** @type {any[]} value objects to free */
  const garbage = [];
  /** @param {any} o */
  const keep = o => (garbage.push(o), o);
  /** @type {Map<string, any>} level -> router */
  const routers = new Map();
  const router = (/** @type {string} */ level) => {
    let r = routers.get(level);
    if (r) return r;
    r = new A.Router(A.RouterFlag.OrthogonalRouting.value);
    r.setRoutingParameter(A.RoutingParameter.shapeBufferDistance, 6);
    r.setRoutingParameter(A.RoutingParameter.idealNudgingDistance, 4);
    r.setRoutingParameter(A.RoutingParameter.segmentPenalty, 30);
    r.setRoutingOption(A.RoutingOption.nudgeOrthogonalSegmentsConnectedToShapes, true);
    r.setRoutingOption(A.RoutingOption.nudgeSharedPathsWithCommonEndPoint, false);
    for (const id of kids.get(level) || []) {
      const b = laid.nodes.get(id);
      if (b) new A.ShapeRef(r, keep(new A.Rectangle(keep(new A.Point(b.x, b.y)), keep(new A.Point(b.x + b.w, b.y + b.h)))));
    }
    routers.set(level, r);
    return r;
  };
  /** @type {Map<string, { conn: any, piece: Piece }[]>} */
  const conns = new Map();
  for (const [key, ps] of split) {
    conns.set(key, ps.map(piece => ({ piece, conn: new A.ConnRef(router(piece.level), keep(new A.ConnEnd(keep(new A.Point(piece.from.x, piece.from.y)))), keep(new A.ConnEnd(keep(new A.Point(piece.to.x, piece.to.y))))) })));
  }
  for (const r of routers.values()) r.processTransaction();

  /** @type {Map<string, Point[]>} */
  const routes = new Map();
  for (const [key, cs] of conns) {
    /** @type {Point[]} */
    const pts = [];
    for (const { conn } of cs) {
      const pl = conn.displayRoute();
      for (let i = 0; i < pl.size(); i++) { const p = pl.at(i); pts.push({ x: p.x, y: p.y }); }
    }
    routes.set(key, pts);
  }
  for (const r of routers.values()) r.delete();
  for (const o of garbage) o.delete();

  const container = new Set(scene.nodes.filter(n => "container" in n && n.container).map(n => n.id));
  const edges = scene.edges.flatMap(e => {
    const raw = routes.get(e.key);
    if (!raw || raw.length < 2) { const old = laid.edges.find(x => x.edge.key === e.key); return old ? [old] : []; }
    let pts = simplify(orthogonal(raw));
    // leaves were routed from their centres: start and end on their borders
    if (!container.has(e.src)) pts = clipStart(pts, /** @type {Box} */ (laid.nodes.get(e.src)));
    if (!container.has(e.dst)) pts = clipStart(pts.slice().reverse(), /** @type {Box} */ (laid.nodes.get(e.dst))).reverse();
    return [{ edge: e, sections: [simplify(pts)] }];
  });
  return { ...laid, edges };
}

/** @type {Promise<Avoid> | null} */
let loading = null;

/** libavoid-js in the browser, loaded once. */
export function loadAvoid() {
  loading ||= (async () => {
    const { AvoidLib } = await import("https://cdn.jsdelivr.net/npm/libavoid-js@0.5.0-beta.5/dist/index.js");
    await AvoidLib.load("https://cdn.jsdelivr.net/npm/libavoid-js@0.5.0-beta.5/dist/libavoid.wasm");
    return AvoidLib.getInstance();
  })();
  return loading;
}

/** @param {Scene} scene @param {Laid} laid @returns {Promise<Laid>} */
export async function route(scene, laid) {
  return routeWith(await loadAvoid(), scene, laid);
}
