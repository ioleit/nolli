// How much of the reader's mental map survives a step from one layout to the next.
//
// Only boxes present in both layouts count (a step's new children have no "before").
// Absolute movement is deliberately not a score: everything shifting because one box
// grew changes nothing for the reader. The scores are about relative position:
//
//   order     share of (pair, axis) relations kept: A left of / overlapping / right of B,
//             and above / overlapping / below (orthogonal ordering, after Misue et al.
//             1995); pairs where one box contains the other are skipped. 1 = all kept.
//   knn       mean share of each box's 3 nearest boxes that are still among its 3
//             nearest. 1 = all kept.
//   residual  movement left after the best shift and uniform scale of the new picture
//             onto the old one, root-mean-square, as a share of the old picture's
//             diagonal. 0 = the new picture is the old one moved and scaled.

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").Laid} Laid
 * @typedef {import("../types/nolli").Box} Box
 * @typedef {import("../types/nolli").Point} Point
 * @typedef {{ n: number, order: number, knn: number, residual: number }} Stability
 */

const K = 3;

/** @param {number} a0 @param {number} a1 @param {number} b0 @param {number} b1 */
function relation(a0, a1, b0, b1) { return a1 <= b0 ? -1 : b1 <= a0 ? 1 : 0; }

/** ancestors(id) -> the drawn containers around id. @param {Scene} scene */
function ancestry(scene) {
  const parent = new Map(scene.nodes.map(n => [n.id, n.parent]));
  return (/** @type {string} */ id) => { /** @type {Set<string>} */ const out = new Set(); let p = parent.get(id); while (p) { out.add(p); p = parent.get(p); } return out; };
}

/** @param {Laid} before @param {Laid} after @param {Scene} sceneBefore @param {Scene} sceneAfter @returns {Stability} */
export function compare(before, after, sceneBefore, sceneAfter) {
  const anc0 = ancestry(sceneBefore), anc1 = ancestry(sceneAfter);
  const anchors = new Set(sceneBefore.nodes.filter(n => n.kind === "anchor").map(n => n.id));  // a module's own code
  const ids = [...before.nodes.keys()].filter(id => after.nodes.has(id) && !anchors.has(id));
  const n = ids.length;
  if (n < 2) return { n, order: 1, knn: 1, residual: 0 };
  const A = ids.map(id => /** @type {Box} */ (before.nodes.get(id))), B = ids.map(id => /** @type {Box} */ (after.nodes.get(id)));
  const ancA = ids.map(anc0), ancB = ids.map(anc1);

  let kept = 0, total = 0;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    if (ancA[i].has(ids[j]) || ancA[j].has(ids[i]) || ancB[i].has(ids[j]) || ancB[j].has(ids[i])) continue;
    const a = A[i], b = A[j], c = B[i], d = B[j];
    kept += Number(relation(a.x, a.x + a.w, b.x, b.x + b.w) === relation(c.x, c.x + c.w, d.x, d.x + d.w));
    kept += Number(relation(a.y, a.y + a.h, b.y, b.y + b.h) === relation(c.y, c.y + c.h, d.y, d.y + d.h));
    total += 2;
  }

  const centre = (/** @type {Box} */ b) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
  const P = A.map(centre), Q = B.map(centre);
  /** @param {Point[]} pts @returns {number[][]} */
  const nearest = pts => pts.map((p, i) => pts.map((q, j) => /** @type {[number, number]} */ ([j, (p.x - q.x) ** 2 + (p.y - q.y) ** 2])).filter(([j]) => j !== i).sort((u, v) => u[1] - v[1] || u[0] - v[0]).slice(0, K).map(([j]) => j));
  const k = Math.min(K, n - 1), nP = nearest(P), nQ = nearest(Q);
  let knn = 0;
  for (let i = 0; i < n; i++) { const s = new Set(nQ[i]); knn += nP[i].filter(j => s.has(j)).length / k; }

  // best shift + uniform scale of Q onto P (least squares), then the RMS of what is left
  const mean = (/** @type {Point[]} */ pts) => ({ x: pts.reduce((s, p) => s + p.x, 0) / n, y: pts.reduce((s, p) => s + p.y, 0) / n });
  const mp = mean(P), mq = mean(Q);
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) { const qx = Q[i].x - mq.x, qy = Q[i].y - mq.y; num += (P[i].x - mp.x) * qx + (P[i].y - mp.y) * qy; den += qx * qx + qy * qy; }
  const s = den ? num / den : 1;
  let sq = 0;
  for (let i = 0; i < n; i++) sq += (P[i].x - mp.x - s * (Q[i].x - mq.x)) ** 2 + (P[i].y - mp.y - s * (Q[i].y - mq.y)) ** 2;
  const xs = A.flatMap(b => [b.x, b.x + b.w]), ys = A.flatMap(b => [b.y, b.y + b.h]);
  const diag = Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) || 1;

  return { n, order: total ? kept / total : 1, knn: knn / n, residual: Math.sqrt(sq / n) / diag };
}

/**
 * Edges drawn on top of each other: of all edge length, the share that lies on a segment
 * of a different edge (horizontal or vertical segments within 1.5 px of each other). Each
 * stretch counts once, however many edges share it, so the result is between 0 and 1.
 * @param {Laid} laid
 */
export function stacked(laid) {
  /** @type {{ e: number, k: "h" | "v", at: number, lo: number, hi: number }[]} */
  const segs = [];
  let total = 0;
  laid.edges.forEach(({ sections }, e) => {
    for (const pts of sections) for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      total += Math.hypot(b.x - a.x, b.y - a.y);
      if (Math.abs(a.y - b.y) < 0.5) segs.push({ e, k: "h", at: a.y, lo: Math.min(a.x, b.x), hi: Math.max(a.x, b.x) });
      else if (Math.abs(a.x - b.x) < 0.5) segs.push({ e, k: "v", at: a.x, lo: Math.min(a.y, b.y), hi: Math.max(a.y, b.y) });
    }
  });
  let shared = 0;
  for (const s of segs) {
    // the parts of s covered by another edge's segment, merged so overlaps count once
    const cover = segs.filter(t => t.e !== s.e && t.k === s.k && Math.abs(t.at - s.at) <= 1.5)
      .map(t => [Math.max(s.lo, t.lo), Math.min(s.hi, t.hi)]).filter(([lo, hi]) => hi > lo).sort((p, q) => p[0] - q[0]);
    let end = -Infinity;
    for (const [lo, hi] of cover) { if (hi <= end) continue; shared += hi - Math.max(lo, end); end = hi; }
  }
  return total ? shared / total : 0;
}
