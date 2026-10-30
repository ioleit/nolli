// Playing a planned step (transition.js) on what draw.js drew: boxes move from where they
// were to where they are, new ones open out of their ancestor, leaving ones shrink into
// theirs, and the view shifts with the clicked box so it holds still on screen. Edges are
// drawn only at the end (fading in), since an old route doesn't turn into a new one.

import { ease, lerpBox } from "./transition.js";
import { getView, setView } from "./panzoom.js";

/**
 * @typedef {import("./transition.js").Plan} Plan
 * @typedef {import("./draw.js").Drawn} Drawn
 * @typedef {import("./draw.js").Handle} Handle
 */

export const DURATION = 380;  // ms
const EDGES_FROM = 0.7;  // edges fade in over the last 30%

/** @type {(() => void) | null} jump the running animation to its end */
let finishRunning = null;

/** End the running animation at once (before the picture is replaced). */
export function finish() { if (finishRunning) finishRunning(); }

/**
 * @param {Plan} plan
 * @param {Drawn} drawn the new picture, drawn at its final positions
 * @param {{ nodes: Map<string, Handle>, remove: () => void }} ghosts copies of the leaving nodes
 * @param {number} duration 0: jump to the end (the view still shifts)
 */
export function play(plan, drawn, ghosts, duration = DURATION) {
  finish();
  const view = getView();
  /** @param {number} t 0..1 */
  const frame = t => {
    const e = ease(t);
    for (const [id, h] of drawn.nodes) {
      const m = plan.nodes.get(id);
      if (!m) continue;
      h.update(lerpBox(m.from, m.to, e));
      if (m.fade === "in") h.opacity(e);
    }
    for (const [id, h] of ghosts.nodes) {
      const m = plan.leaving.get(id);
      if (m) { h.update(lerpBox(m.from, m.to, e)); h.opacity(1 - e); }
    }
    const eo = t < EDGES_FROM ? 0 : (t - EDGES_FROM) / (1 - EDGES_FROM);
    for (const x of drawn.edges) x.setAttribute("opacity", String(eo));
    setView({ k: view.k, x: view.x + view.k * plan.shift.x * e, y: view.y + view.k * plan.shift.y * e });
  };
  let raf = 0;
  const done = () => {
    cancelAnimationFrame(raf);
    frame(1);
    // leave the DOM exactly as draw() made it
    for (const h of drawn.nodes.values()) h.opacity(null);
    for (const x of drawn.edges) x.removeAttribute("opacity");
    ghosts.remove();
    finishRunning = null;
  };
  if (duration <= 0) { done(); return; }
  finishRunning = done;
  const t0 = performance.now();
  frame(0);  // before the first paint: no flash of the final picture
  const tick = () => {
    const t = Math.min(1, (performance.now() - t0) / duration);
    if (t >= 1) { done(); return; }
    frame(t);
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
}
