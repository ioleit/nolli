// Pan (drag) and zoom (wheel) by a transform on the root group.

const svg = document.getElementById("g");
const root = document.getElementById("root");
let zoom = { k: 1, x: 0, y: 0 };
let drag = null;

function apply() { root.setAttribute("transform", `translate(${zoom.x},${zoom.y}) scale(${zoom.k})`); }

// fit a w × h drawing into the canvas, never magnifying beyond 1.5
export function fit(w, h) {
  const r = svg.getBoundingClientRect(); const k = Math.min((r.width - 24) / w, (r.height - 24) / h, 1.5);
  zoom = { k, x: (r.width - w * k) / 2, y: 12 }; apply();
}

svg.addEventListener("mousedown", ev => { drag = { x: ev.clientX - zoom.x, y: ev.clientY - zoom.y }; });
window.addEventListener("mouseup", () => drag = null);
window.addEventListener("mousemove", ev => { if (drag) { zoom.x = ev.clientX - drag.x; zoom.y = ev.clientY - drag.y; apply(); } });
svg.addEventListener("wheel", ev => {
  ev.preventDefault(); const f = ev.deltaY < 0 ? 1.1 : 1 / 1.1; const r = svg.getBoundingClientRect(); const mx = ev.clientX - r.left, my = ev.clientY - r.top;
  zoom.x = mx - (mx - zoom.x) * f; zoom.y = my - (my - zoom.y) * f; zoom.k *= f; apply();
}, { passive: false });
