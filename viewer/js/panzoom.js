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

/** Fit box b (drawing units) into the canvas, never magnifying beyond 1.5, centred. @param {{ x: number, y: number, w: number, h: number }} b */
export function fitBox(b) {
  const r = svg.getBoundingClientRect(), pad = 40; const k = Math.min((r.width - 2 * pad) / b.w, (r.height - 2 * pad) / b.h, 1.5);
  zoom = { k, x: r.width / 2 - (b.x + b.w / 2) * k, y: r.height / 2 - (b.y + b.h / 2) * k }; apply();
}

svg.addEventListener("mousedown", ev => { drag = { x: ev.clientX - zoom.x, y: ev.clientY - zoom.y }; });
window.addEventListener("mouseup", () => drag = null);
window.addEventListener("mousemove", ev => { if (drag) { zoom.x = ev.clientX - drag.x; zoom.y = ev.clientY - drag.y; apply(); } });
svg.addEventListener("wheel", ev => {
  ev.preventDefault(); const f = ev.deltaY < 0 ? 1.1 : 1 / 1.1; const r = svg.getBoundingClientRect(); const mx = ev.clientX - r.left, my = ev.clientY - r.top;
  zoom.x = mx - (mx - zoom.x) * f; zoom.y = my - (my - zoom.y) * f; zoom.k *= f; apply();
}, { passive: false });
