// The sidebar: dataset meta, folded groups, roles, residue, and the controls' checked state.

import { byId, input, select } from "./dom.js";
import { ALL_ROLES, OPTIONS, state } from "./state.js";

/** @typedef {import("../types/nolli").Scene} Scene */

/**
 * @param {Scene} scene
 * @param {(id: string) => void} onToggle unfold or fold a group
 * @param {() => void} onRoles the shown roles changed
 */
export function sidebar(scene, onToggle, onRoles) {
  const d = scene.dataset;
  byId("meta").textContent = `${(d.repo || "").split("/").pop()} @ ${d.commit || "?"} · ${d.files ?? "?"} files · ${d.entities} entities · ${d.relations} relations`;
  const ul = byId("residue"); ul.innerHTML = "";
  for (const s of scene.residue) { const li = document.createElement("li"); li.textContent = s; ul.appendChild(li); }
  const gd = byId("groups"); gd.innerHTML = "";
  for (const gr of scene.groups) {
    const chip = document.createElement("span"); chip.className = "chip";
    chip.appendChild(document.createTextNode(`${gr.name} (${gr.size})`));
    const b = document.createElement("button"); b.textContent = gr.open ? "fold" : "unfold"; b.addEventListener("click", () => onToggle(gr.id)); chip.appendChild(b);
    gd.appendChild(chip);
  }
  const rd = byId("roles"); rd.innerHTML = "";
  const roles = state.roles || [];
  for (const r of ALL_ROLES) {
    const l = document.createElement("label"), c = document.createElement("input"); c.type = "checkbox"; c.checked = roles.includes(r);
    c.addEventListener("change", () => { const now = state.roles || []; state.roles = c.checked ? [...now, r] : now.filter(x => x !== r); onRoles(); });
    l.appendChild(c); l.appendChild(document.createTextNode(" " + r)); rd.appendChild(l);
  }
}

export function syncControls() {
  input("budget").value = String(state.budget);
  for (const [id, k] of Object.entries(OPTIONS)) input(id).checked = state[k];
  input("nolabels").checked = state.noLabels; byId("g").classList.toggle("nolabels", state.noLabels);
  input("animate").checked = state.animate;
  input("raw").checked = state.raw;
  select("direction").value = state.direction;
  select("engine").value = state.engine;
}
