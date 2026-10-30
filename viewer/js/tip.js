// Tooltip next to the pointer, positioned within the canvas.

const tipEl = document.getElementById("tip");
const svg = document.getElementById("g");

export function showTip(ev, text) {
  tipEl.style.display = "block"; tipEl.textContent = text;
  const r = svg.getBoundingClientRect();
  tipEl.style.left = Math.min(ev.clientX - r.left + 12, r.width - 470) + "px"; tipEl.style.top = (ev.clientY - r.top + 12) + "px";
}

export function hideTip() { tipEl.style.display = "none"; }
