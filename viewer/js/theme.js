// Colour themes: CSS only (viewer.css variables, themes.css); this sets which one applies.

import { select } from "./dom.js";

/** @type {Record<string, string>} theme name -> label; "default" is viewer.css's :root */
export const THEMES = { default: "Default", textbook: "Textbook" };
const KEY = "nolli.theme";

/** The theme to start with: the link's, else the last one chosen in this browser. @param {string | null} fromHash */
export function initialTheme(fromHash) {
  let stored = null;
  try { stored = localStorage.getItem(KEY); } catch { /* storage blocked */ }
  const t = fromHash || stored || "default";
  return t in THEMES ? t : "default";
}

/** @param {string} t */
export function applyTheme(t) {
  if (t === "default") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem(KEY, t); } catch { /* storage blocked */ }
}

/** Fill the Theme select. */
export function themeOptions() {
  const s = select("theme");
  for (const [k, label] of Object.entries(THEMES)) { const o = document.createElement("option"); o.value = k; o.textContent = label; s.appendChild(o); }
}
