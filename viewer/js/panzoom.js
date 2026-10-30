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
export function setView(v) { zoom = { ...v }; apply(); }

function apply() { root.setAttribute("transform", `translate(${zoom.x},${zoom.y}) scale(${zoom.k})`); }

/** Fit a w × h drawing into the canvas, never magnifying beyond 1.5. @param {number} w @param {number} h */
export function fit(w, h) {
  const r = svg.getBoundingClientRect(); const k = Math.min((r.width - 24) / w, (r.height - 24) / h, 1.5);
  zoom = { k, x: (r.width - w * k) / 2, y: 12 }; apply();
}

let moved = 0;
svg.addEventListener("mousedown", ev => { drag = { x: ev.clientX - zoom.x, y: ev.clientY - zoom.y }; moved = 0; });
window.addEventListener("mouseup", () => drag = null);
window.addEventListener("mousemove", ev => { if (drag) { const nx = ev.clientX - drag.x, ny = ev.clientY - drag.y; moved += Math.abs(nx - zoom.x) + Math.abs(ny - zoom.y); zoom.x = nx; zoom.y = ny; apply(); } });
/** True if the pointer moved while the button was down: the click that ends a pan is not a click. */
export const wasDrag = () => moved > 4;
svg.addEventListener("wheel", ev => {
  ev.preventDefault(); const f = ev.deltaY < 0 ? 1.1 : 1 / 1.1; const r = svg.getBoundingClientRect(); const mx = ev.clientX - r.left, my = ev.clientY - r.top;
  zoom.x = mx - (mx - zoom.x) * f; zoom.y = my - (my - zoom.y) * f; zoom.k *= f; apply();
}, { passive: false });

/**
 * Centre the view on some boxes, zoomed so that they fit, at most to 1.2 (readable, not huge).
 * @param {{ x: number, y: number, w: number, h: number }[]} boxes in drawing coordinates
 */
export function focusOn(boxes) {
  if (!boxes.length) return;
  const x0 = Math.min(...boxes.map(b => b.x)), y0 = Math.min(...boxes.map(b => b.y));
  const x1 = Math.max(...boxes.map(b => b.x + b.w)), y1 = Math.max(...boxes.map(b => b.y + b.h));
  const r = svg.getBoundingClientRect(), pad = 60;
  const k = Math.max(0.05, Math.min(1.2, (r.width - pad) / Math.max(1, x1 - x0), (r.height - pad) / Math.max(1, y1 - y0)));
  zoom = { k, x: r.width / 2 - ((x0 + x1) / 2) * k, y: r.height / 2 - ((y0 + y1) / 2) * k }; apply();
}
