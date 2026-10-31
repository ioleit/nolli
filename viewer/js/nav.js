// The sidebar folds to a thin strip, the default, so the picture gets the width on a
// laptop; opening it is kept in this browser.

import { byId } from "./dom.js";

const KEY = "nolli.nav-open";
const btn = byId("navtoggle");

/** @param {boolean} open */
function setNav(open) {
  document.body.classList.toggle("navclosed", !open);
  btn.setAttribute("aria-expanded", String(open));
  btn.textContent = open ? "«" : "»";
  btn.dataset.help = open ? "Fold the controls away." : "Show the controls: dataset, what to show, detail, theme, residue.";
}

let stored = null;
try { stored = localStorage.getItem(KEY); } catch { /* storage blocked */ }
setNav(stored === "1");
btn.addEventListener("click", () => {
  const open = document.body.classList.contains("navclosed");
  setNav(open);
  try { localStorage.setItem(KEY, open ? "1" : "0"); } catch { /* storage blocked */ }
});
