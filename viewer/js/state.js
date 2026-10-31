// View state: what the viewer asks the server for, plus the drawing-only settings.
// The location hash is the scene query plus those settings, so a URL is a view.

/**
 * @typedef {"diagram" | "json" | "mermaid"} View
 * @typedef {{ expanded: string[] | null, roles: string[] | null, groups: string[] | null, budget: number | null, utilities: number | null,
 *             side: "both" | "base" | "head" }} SceneQuery
 * @typedef {SceneQuery & { name: string | null, theme: string, view: View, item: string | null, dim: boolean }} ViewState
 *   item: the review item last gone to ("C3"); dim: fade what did not change (diff datasets)
 */

// expanded / roles / groups / budget / utilities null: let the server choose (default cut, roles, groups, threshold)
/** @type {SceneQuery} */
export const DEFAULTS = { expanded: null, roles: null, groups: null, budget: null, utilities: null, side: "both" };
export const DEFAULT_THEME = "textbook";
/** @type {ViewState} */
export const state = { name: null, ...DEFAULTS, theme: DEFAULT_THEME, view: "diagram", item: null, dim: true };
/** @type {View[]} */
export const VIEWS = ["diagram", "json", "mermaid"];
export const ALL_ROLES = ["source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other"];

// the /api/scene parameters (nolli/scene.py state_from_query)
export function query() {
  const p = new URLSearchParams();
  p.set("d", state.name || "");
  if (state.expanded) p.set("x", state.expanded.join(","));
  if (state.roles) p.set("r", state.roles.join(","));
  if (state.groups) p.set("g", state.groups.join(","));
  if (state.budget) p.set("b", String(state.budget));
  if (state.utilities !== null) p.set("ut", String(state.utilities));
  if (state.side !== "both") p.set("s", state.side);
  return p;
}

export function writeHash() {
  const p = query();
  if (state.theme !== DEFAULT_THEME) p.set("th", state.theme); if (state.view !== "diagram") p.set("v", state.view);
  if (state.item) p.set("item", state.item); if (!state.dim) p.set("nodim", "1");
  history.replaceState(null, "", "#" + p.toString());
}

/** @param {string} h the location hash without "#" */
export function readHash(h) {
  const p = new URLSearchParams(h);
  const st = { ...DEFAULTS };
  const x = p.get("x"), r = p.get("r"), g = p.get("g"), b = p.get("b");
  if (x) st.expanded = x.split(",").filter(Boolean);
  if (r !== null) st.roles = r.split(",").filter(Boolean);
  if (g !== null) st.groups = g.split(",").filter(Boolean);
  if (b) st.budget = +b;
  st.utilities = p.get("nu") === "1" ? 0 : p.get("ut") ? +(p.get("ut") || 0) : null;
  const side = p.get("s"); st.side = side === "base" || side === "head" ? side : "both";
  // older links: raw=1, mmd=1
  const v = p.get("v") || (p.get("raw") === "1" ? "json" : p.get("mmd") === "1" ? "mermaid" : "diagram");
  return { name: p.get("d"), st, theme: p.get("th"),
    view: /** @type {View} */ (VIEWS.includes(/** @type {View} */ (v)) ? v : "diagram"), item: p.get("item"), dim: p.get("nodim") !== "1" };
}
