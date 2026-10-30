// Tooltip next to the pointer, positioned within the canvas.

import { byId } from "./dom.js";

const tipEl = byId("tip");
const svg = byId("g");

/** @param {MouseEvent} ev @param {string} text */
export function showTip(ev, text) {
  tipEl.style.display = "block"; tipEl.textContent = text;
  const r = svg.getBoundingClientRect();
  tipEl.style.left = Math.min(ev.clientX - r.left + 12, r.width - 470) + "px"; tipEl.style.top = (ev.clientY - r.top + 12) + "px";
}

export function hideTip() { tipEl.style.display = "none"; }
