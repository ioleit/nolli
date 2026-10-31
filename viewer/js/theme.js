// Colour themes: CSS only (viewer.css variables, themes.css); this sets which one applies.

import { select } from "./dom.js";
import { DEFAULT_THEME } from "./state.js";

/** @type {Record<string, string>} theme name -> label; "default" is viewer.css's :root */
export const THEMES = { textbook: "Textbook", default: "Plain" };
// only a theme chosen in the Theme control is kept (an older key stored whatever was shown,
// which would keep everyone on the old default)
const KEY = "nolli.theme-chosen";

/** The theme to start with: the link's, else the last one chosen in this browser. @param {string | null} fromHash */
export function initialTheme(fromHash) {
  let stored = null;
  try { stored = localStorage.getItem(KEY); } catch { /* storage blocked */ }
  const t = fromHash || stored || DEFAULT_THEME;
  return t in THEMES ? t : DEFAULT_THEME;
}

/** @param {string} t */
export function applyTheme(t) {
  if (t === "default") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
}

/** A theme chosen in the control: applied and kept in this browser. @param {string} t */
export function chooseTheme(t) {
  applyTheme(t);
  try { localStorage.setItem(KEY, t); } catch { /* storage blocked */ }
}

/** Fill the Theme select. */
export function themeOptions() {
  const s = select("theme");
  for (const [k, label] of Object.entries(THEMES)) { const o = document.createElement("option"); o.value = k; o.textContent = label; s.appendChild(o); }
}
