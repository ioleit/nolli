// Planning the step from one picture to the next: where every box starts and ends.
// Pure (no DOM); the animation only interpolates what this decides.
//
// - A box in both pictures moves from its old box to its new one.
// - A new box (an unfolded box's children, say) starts inside the old box of its nearest
//   ancestor that was already drawn, at its relative position scaled into that box, and
//   fades in: unfolding looks like the box opening up. Without such an ancestor it fades
//   in where it ends.
// - A box that leaves shrinks into the new box of its nearest ancestor that is still
//   drawn (folding is the reverse of unfolding) and fades out.
// - The anchor, the box that was clicked, keeps its top-left corner on screen: the view
//   shifts by exactly as much as the anchor moves, in step with it.

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").Laid} Laid
 * @typedef {import("../types/nolli").Box} Box
 * @typedef {{ from: Box, to: Box, fade: "in" | "out" | null }} Move
 * @typedef {{ nodes: Map<string, Move>, leaving: Map<string, Move>, anchor: string | null, shift: { x: number, y: number } }} Plan
 */

/** b, given relative to box `from`, placed the same way relative to box `to`. @param {Box} b @param {Box} from @param {Box} to @returns {Box} */
export function rebase(b, from, to) {
  const sx = from.w ? to.w / from.w : 1, sy = from.h ? to.h / from.h : 1;
  return { x: to.x + (b.x - from.x) * sx, y: to.y + (b.y - from.y) * sy, w: b.w * sx, h: b.h * sy };
}

/** id -> parent id, from a scene. @param {Scene} scene */
const parents = scene => new Map(scene.nodes.map(n => [n.id, n.parent]));

/**
 * The nearest ancestor of id (by `parent`) that has a box in `laid`.
 * @param {string} id @param {Map<string, string | null>} parent @param {Laid} laid
 */
function nearestIn(id, parent, laid) {
  let p = parent.get(id);
  while (p) { if (laid.nodes.has(p)) return p; p = parent.get(p); }
  return null;
}

/**
 * @param {Scene} prevScene @param {Laid} prev the picture shown now
 * @param {Scene} nextScene @param {Laid} next the picture to show
 * @param {string | null} [clicked] the box that was unfolded or folded
 * @returns {Plan}
 */
export function plan(prevScene, prev, nextScene, next, clicked = null) {
  const prevParent = parents(prevScene), nextParent = parents(nextScene);
  /** @type {Map<string, Move>} */
  const nodes = new Map();
  for (const [id, to] of next.nodes) {
    const was = prev.nodes.get(id);
    if (was) { nodes.set(id, { from: was, to, fade: null }); continue; }
    const a = nearestIn(id, nextParent, prev);
    const from = a ? rebase(to, /** @type {Box} */ (next.nodes.get(a)), /** @type {Box} */ (prev.nodes.get(a))) : to;
    nodes.set(id, { from, to, fade: "in" });
  }
  /** @type {Map<string, Move>} */
  const leaving = new Map();
  for (const [id, from] of prev.nodes) {
    if (next.nodes.has(id)) continue;
    const a = nearestIn(id, prevParent, next);
    const to = a ? rebase(from, /** @type {Box} */ (prev.nodes.get(a)), /** @type {Box} */ (next.nodes.get(a))) : from;
    leaving.set(id, { from, to, fade: "out" });
  }
  const anchor = clicked && prev.nodes.has(clicked) && next.nodes.has(clicked) ? clicked : null;
  const shift = { x: 0, y: 0 };
  if (anchor) {
    const a = /** @type {Box} */ (prev.nodes.get(anchor)), b = /** @type {Box} */ (next.nodes.get(anchor));
    shift.x = a.x - b.x; shift.y = a.y - b.y;
  }
  return { nodes, leaving, anchor, shift };
}

/** @param {number} t 0..1 */
export const ease = t => t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;

/** Exactly a at t = 0 and exactly b at t = 1 (no rounding left in the final picture). @param {Box} a @param {Box} b @param {number} t */
export const lerpBox = (a, b, t) => t <= 0 ? { ...a } : t >= 1 ? { ...b } : ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, w: a.w + (b.w - a.w) * t, h: a.h + (b.h - a.h) * t });
