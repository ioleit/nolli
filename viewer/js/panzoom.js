// Pan (drag) and zoom (wheel) by a transform on the root group.

import { byId } from "./dom.js";

const svg = byId("g");
const root = byId("root");
let zoom = { k: 1, x: 0, y: 0 };
/** @type {{ x: number, y: number } | null} */
let drag = null;

/** The current view: scale k, then translate (x, y) in screen pixels. */
export const getView = () => ({ ...zoom });
/** @param {{ k: number, x: number, y: number }} v */
export function setView(v) { stopGlide(); zoom = { ...v }; apply(); }

/** @type {number} the running glide's animation frame, 0 if none */
let glide = 0;
function stopGlide() { if (glide) cancelAnimationFrame(glide); glide = 0; }

function apply() { root.setAttribute("transform", `translate(${zoom.x},${zoom.y}) scale(${zoom.k})`); }

/** Fit a w × h drawing into the canvas, never magnifying beyond 1.5. @param {number} w @param {number} h */
export function fit(w, h) {
  stopGlide();
  const r = svg.getBoundingClientRect(); const k = Math.min((r.width - 24) / w, (r.height - 24) / h, 1.5);
  zoom = { k, x: (r.width - w * k) / 2, y: 12 }; apply();
}

let moved = 0;
svg.addEventListener("mousedown", ev => { stopGlide(); drag = { x: ev.clientX - zoom.x, y: ev.clientY - zoom.y }; moved = 0; });
window.addEventListener("mouseup", () => drag = null);
window.addEventListener("mousemove", ev => { if (drag) { const nx = ev.clientX - drag.x, ny = ev.clientY - drag.y; moved += Math.abs(nx - zoom.x) + Math.abs(ny - zoom.y); zoom.x = nx; zoom.y = ny; apply(); } });
/** True if the pointer moved while the button was down: the click that ends a pan is not a click. */
export const wasDrag = () => moved > 4;
svg.addEventListener("wheel", ev => {
  ev.preventDefault(); stopGlide(); const f = ev.deltaY < 0 ? 1.1 : 1 / 1.1; const r = svg.getBoundingClientRect(); const mx = ev.clientX - r.left, my = ev.clientY - r.top;
  zoom.x = mx - (mx - zoom.x) * f; zoom.y = my - (my - zoom.y) * f; zoom.k *= f; apply();
}, { passive: false });

/**
 * Centre the view on some boxes, zoomed so that they fit, at most to 1.2 (readable, not huge).
 * With a duration, glide there: the centre moves and the zoom changes evenly (geometrically),
 * eased at both ends; a drag or the wheel stops it where it is.
 * @param {{ x: number, y: number, w: number, h: number }[]} boxes in drawing coordinates
 * @param {number} [duration] ms; 0 jumps
 * @returns {number} how long the move takes
 */
export function focusOn(boxes, duration = 0) {
  if (!boxes.length) return 0;
  const x0 = Math.min(...boxes.map(b => b.x)), y0 = Math.min(...boxes.map(b => b.y));
  const x1 = Math.max(...boxes.map(b => b.x + b.w)), y1 = Math.max(...boxes.map(b => b.y + b.h));
  const r = svg.getBoundingClientRect(), pad = 60;
  const k = Math.max(0.05, Math.min(1.2, (r.width - pad) / Math.max(1, x1 - x0), (r.height - pad) / Math.max(1, y1 - y0)));
  stopGlide();
  if (duration <= 0) { zoom = { k, x: r.width / 2 - ((x0 + x1) / 2) * k, y: r.height / 2 - ((y0 + y1) / 2) * k }; apply(); return 0; }
  // the view's centre in drawing coordinates, from where it is now to the boxes' centre
  const W = r.width / 2, H = r.height / 2, k0 = zoom.k;
  const c0 = { x: (W - zoom.x) / k0, y: (H - zoom.y) / k0 }, c1 = { x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
  const t0 = performance.now();
  const ease = (/** @type {number} */ t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const tick = () => {
    const t = Math.min(1, (performance.now() - t0) / duration), e = ease(t);
    const kk = k0 * Math.pow(k / k0, e), cx = c0.x + (c1.x - c0.x) * e, cy = c0.y + (c1.y - c0.y) * e;
    zoom = { k: kk, x: W - cx * kk, y: H - cy * kk }; apply();
    glide = t < 1 ? requestAnimationFrame(tick) : 0;
  };
  glide = requestAnimationFrame(tick);
  return duration;
}
