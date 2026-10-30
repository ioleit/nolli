// View state: what the viewer asks the server for, plus the drawing-only settings.
// The location hash is the scene query plus those settings, so a URL is a view.

/**
 * @typedef {{ expanded: string[] | null, roles: string[] | null, budget: number | null, through: boolean, danglingUse: boolean,
 *             envEdges: boolean, bundle: boolean, utilities: boolean, hideInternal: boolean, side: "both" | "base" | "head" }} SceneQuery
 * @typedef {SceneQuery & { name: string | null, noLabels: boolean, direction: "DOWN" | "RIGHT", engine: string, animate: boolean, raw: boolean,
 *             sel: string | null, dim: boolean }} ViewState
 *   sel: what the review panel shows, "item:C3", "node:<entity id>" or "edge:<edge key>"; dim: fade what did not change
 * @typedef {"through" | "danglingUse" | "envEdges" | "bundle" | "utilities" | "hideInternal"} OptionKey
 */

// expanded / roles / budget null: let the server choose (default cut, default roles)
/** @type {SceneQuery} */
export const DEFAULTS = { expanded: null, roles: null, budget: null, through: true, danglingUse: false, envEdges: false, bundle: true, utilities: true, hideInternal: false, side: "both" };
/** @type {ViewState} */
export const state = { name: null, ...DEFAULTS, noLabels: false, direction: "DOWN", engine: "elk", animate: true, raw: false, sel: null, dim: true };
export const ALL_ROLES = ["source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other"];
// option checkbox id -> state key; each is a scene parameter
/** @type {Record<string, OptionKey>} */
export const OPTIONS = { through: "through", dangling: "danglingUse", bundle: "bundle", envedges: "envEdges", utilities: "utilities", hideinternal: "hideInternal" };

// the /api/scene parameters (nolli/scene.py state_from_query)
export function query() {
  const p = new URLSearchParams();
  p.set("d", state.name || "");
  if (state.expanded) p.set("x", state.expanded.join(","));
  if (state.roles) p.set("r", state.roles.join(","));
  if (state.budget) p.set("b", String(state.budget));
  if (!state.through) p.set("t", "0");
  if (state.danglingUse) p.set("u", "1");
  if (state.envEdges) p.set("ev", "1");
  if (!state.bundle) p.set("nb", "1");
  if (!state.utilities) p.set("nu", "1");
  if (state.hideInternal) p.set("hi", "1");
  if (state.side !== "both") p.set("s", state.side);
  return p;
}

export function writeHash() {
  const p = query();
  if (state.noLabels) p.set("nl", "1"); if (state.direction !== "DOWN") p.set("dir", state.direction); if (state.engine !== "elk") p.set("lay", state.engine); if (!state.animate) p.set("an", "0"); if (state.raw) p.set("raw", "1");
  if (state.sel) p.set("sel", state.sel); if (!state.dim) p.set("nodim", "1");
  history.replaceState(null, "", "#" + p.toString());
}

/** @param {string} h the location hash without "#" */
export function readHash(h) {
  const p = new URLSearchParams(h);
  const st = { ...DEFAULTS };
  const x = p.get("x"), r = p.get("r"), b = p.get("b");
  if (x) st.expanded = x.split(",").filter(Boolean);
  if (r !== null) st.roles = r.split(",").filter(Boolean);
  if (b) st.budget = +b;
  st.through = p.get("t") !== "0"; st.danglingUse = p.get("u") === "1"; st.envEdges = p.get("ev") === "1";
  st.bundle = p.get("nb") !== "1"; st.utilities = p.get("nu") !== "1"; st.hideInternal = p.get("hi") === "1";
  const side = p.get("s"); st.side = side === "base" || side === "head" ? side : "both";
  return { name: p.get("d"), st, noLabels: p.get("nl") === "1", direction: /** @type {"DOWN" | "RIGHT"} */ (p.get("dir") === "RIGHT" ? "RIGHT" : "DOWN"), engine: p.get("lay") || "elk", animate: p.get("an") !== "0", raw: p.get("raw") === "1", mmd: p.get("mmd") === "1", sel: p.get("sel"), dim: p.get("nodim") !== "1" };
}
