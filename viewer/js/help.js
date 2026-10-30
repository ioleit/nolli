// Explanations for the controls: any element with data-help shows it in a box next to it
// on hover, so the labels themselves can stay one or two words.

import { byId } from "./dom.js";

const box = byId("help");

document.addEventListener("mouseover", ev => {
  const t = /** @type {HTMLElement | null} */ (/** @type {Element} */ (ev.target).closest("[data-help]"));
  if (!t || !t.dataset.help) { box.style.display = "none"; return; }
  box.textContent = t.dataset.help; box.style.display = "block";
  const r = t.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
  // to the right of the sidebar control; below a tab
  const right = r.right + 8 + w < innerWidth;
  const left = t.closest("aside") && right ? r.right + 8 : Math.max(4, Math.min(r.left, innerWidth - w - 4));
  const top = t.closest("aside") && right ? r.top : r.bottom + 6;
  box.style.left = left + "px"; box.style.top = Math.max(4, Math.min(top, innerHeight - h - 4)) + "px";
});
document.addEventListener("mouseleave", () => { box.style.display = "none"; });
