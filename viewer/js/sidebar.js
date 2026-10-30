// The sidebar: dataset meta, the Show filter (groups, roles), residue, and the controls' checked state.

import { byId, input, select } from "./dom.js";
import { ALL_ROLES, OPTIONS, state } from "./state.js";

/** @typedef {import("../types/nolli").Scene} Scene */

/** @type {Record<string, string>} what showing a group means; the server names the groups */
const GROUP_HELP = {
  http: "Routes this repo serves, as one box you can unfold into route groups and endpoints. Calls go into it, and from it to the handlers that realise them. Off: calls are drawn straight to the handler (Through interfaces) or become badges.",
  env: "Environment variables, as one box you can unfold. Readers point into it, and from it to whatever sets them. Off: badges (\"N env\") on the readers.",
  deploy: "Compose services and images, as one box you can unfold. Off: badges on the code they touch.",
  ext: "Third-party packages, as one box you can unfold. Off: badges (\"N pkgs\") on the importers.",
};

/**
 * @param {Scene} scene
 * @param {() => void} onFilter the shown groups or roles changed
 */
export function sidebar(scene, onFilter) {
  const d = scene.dataset;
  byId("meta").textContent = `${(d.repo || "").split("/").pop()} @ ${d.commit || "?"} · ${d.files ?? "?"} files · ${d.entities} entities · ${d.relations} relations`;
  const ul = byId("residue"); ul.innerHTML = "";
  for (const s of scene.residue) { const li = document.createElement("li"); li.textContent = s; ul.appendChild(li); }

  const gd = byId("groups"); gd.innerHTML = "";
  const shown = scene.state.groups;
  for (const gr of scene.groups) {
    const c = check(gd, gr.name, shown.includes(gr.id), GROUP_HELP[gr.id] || `${gr.name}: one box you can unfold. Off: badges on the boxes that use it.`, String(gr.size));
    c.addEventListener("change", () => { const now = state.groups || shown; state.groups = c.checked ? [...now, gr.id] : now.filter(x => x !== gr.id); onFilter(); });
  }
  if (!scene.groups.length) gd.innerHTML = `<div class="muted">no groups in this repository</div>`;

  const rd = byId("roles"); rd.innerHTML = "";
  const roles = state.roles || [];
  for (const r of ALL_ROLES) {
    const c = check(rd, r, roles.includes(r));
    c.addEventListener("change", () => { const now = state.roles || []; state.roles = c.checked ? [...now, r] : now.filter(x => x !== r); onFilter(); });
  }
  byId("rolesum").textContent = `${roles.length} of ${ALL_ROLES.length}`;
}

/** A checkbox row. @param {HTMLElement} parent @param {string} text @param {boolean} checked @param {string} [help] @param {string} [note] */
function check(parent, text, checked, help, note) {
  const l = document.createElement("label"), c = document.createElement("input");
  l.className = "opt"; if (help) l.dataset.help = help;
  c.type = "checkbox"; c.checked = checked;
  l.append(c, " " + text);
  if (note) { const s = document.createElement("span"); s.className = "muted"; s.textContent = " " + note; l.append(s); }
  parent.appendChild(l);
  return c;
}

export function syncControls() {
  input("budget").value = String(state.budget);
  for (const [id, k] of Object.entries(OPTIONS)) input(id).checked = state[k];
  input("nolabels").checked = state.noLabels; byId("g").classList.toggle("nolabels", state.noLabels);
  input("animate").checked = state.animate;
  select("direction").value = state.direction;
  select("engine").value = state.engine;
  for (const b of document.querySelectorAll("#views [data-view]")) b.setAttribute("aria-selected", String(/** @type {HTMLElement} */ (b).dataset.view === state.view));
}
