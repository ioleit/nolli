// View state: what the viewer asks the server for, plus the drawing-only settings.
// The location hash is the scene query plus those settings, so a URL is a view.

/**
 * @typedef {"diagram" | "json" | "mermaid"} View
 * @typedef {{ expanded: string[] | null, roles: string[] | null, groups: string[] | null, budget: number | null, through: boolean, danglingUse: boolean,
 *             envEdges: boolean, bundle: boolean, utilities: number | null, hideInternal: boolean }} SceneQuery
 * @typedef {SceneQuery & { name: string | null, noLabels: boolean, sameWidth: boolean, theme: string, direction: "DOWN" | "RIGHT", engine: string, animate: boolean, view: View }} ViewState
 * @typedef {"through" | "danglingUse" | "envEdges" | "bundle" | "hideInternal"} OptionKey
 */

// expanded / roles / groups / budget null: let the server choose (default cut, roles, groups)
/** @type {SceneQuery} */
export const DEFAULTS = { expanded: null, roles: null, groups: null, budget: null, through: true, danglingUse: false, envEdges: false, bundle: true, utilities: null, hideInternal: false };
/** @type {ViewState} */
export const state = { name: null, ...DEFAULTS, noLabels: true, sameWidth: false, theme: "default", direction: "DOWN", engine: "elk", animate: true, view: "diagram" };
/** @type {View[]} */
export const VIEWS = ["diagram", "json", "mermaid"];
export const ALL_ROLES = ["source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other"];
// option checkbox id -> state key; each is a scene parameter
/** @type {Record<string, OptionKey>} */
export const OPTIONS = { through: "through", dangling: "danglingUse", bundle: "bundle", envedges: "envEdges", hideinternal: "hideInternal" };

// the /api/scene parameters (nolli/scene.py state_from_query)
export function query() {
  const p = new URLSearchParams();
  p.set("d", state.name || "");
  if (state.expanded) p.set("x", state.expanded.join(","));
  if (state.roles) p.set("r", state.roles.join(","));
  if (state.groups) p.set("g", state.groups.join(","));
  if (state.budget) p.set("b", String(state.budget));
  if (!state.through) p.set("t", "0");
  if (state.danglingUse) p.set("u", "1");
  if (state.envEdges) p.set("ev", "1");
  if (!state.bundle) p.set("nb", "1");
  if (state.utilities !== null) p.set("ut", String(state.utilities));
  if (state.hideInternal) p.set("hi", "1");
  return p;
}

export function writeHash() {
  const p = query();
  if (!state.noLabels) p.set("nl", "0"); if (state.sameWidth) p.set("sw", "1"); if (state.theme !== "default") p.set("th", state.theme); if (state.direction !== "DOWN") p.set("dir", state.direction); if (state.engine !== "elk") p.set("lay", state.engine); if (!state.animate) p.set("an", "0"); if (state.view !== "diagram") p.set("v", state.view);
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
  st.through = p.get("t") !== "0"; st.danglingUse = p.get("u") === "1"; st.envEdges = p.get("ev") === "1";
  st.bundle = p.get("nb") !== "1"; st.utilities = p.get("nu") === "1" ? 0 : p.get("ut") ? +(p.get("ut") || 0) : null; st.hideInternal = p.get("hi") === "1";
  // older links: raw=1, mmd=1
  const v = p.get("v") || (p.get("raw") === "1" ? "json" : p.get("mmd") === "1" ? "mermaid" : "diagram");
  return { name: p.get("d"), st, noLabels: p.get("nl") !== "0", sameWidth: p.get("sw") === "1", theme: p.get("th"), direction: /** @type {"DOWN" | "RIGHT"} */ (p.get("dir") === "RIGHT" ? "RIGHT" : "DOWN"), engine: p.get("lay") || "elk", animate: p.get("an") !== "0",
    view: /** @type {View} */ (VIEWS.includes(/** @type {View} */ (v)) ? v : "diagram") };
}
