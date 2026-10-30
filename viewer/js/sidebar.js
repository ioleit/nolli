// The sidebar: dataset meta, folded groups, roles, residue, and the controls' checked state.

import { ALL_ROLES, OPTIONS, state } from "./state.js";

const $ = id => document.getElementById(id);

// onToggle(id): unfold or fold a group; onRoles(): the shown roles changed
export function sidebar(scene, onToggle, onRoles) {
  const d = scene.dataset;
  $("meta").textContent = `${(d.repo || "").split("/").pop()} @ ${d.commit || "?"} · ${d.files ?? "?"} files · ${d.entities} entities · ${d.relations} relations`;
  const ul = $("residue"); ul.innerHTML = "";
  for (const s of scene.residue) { const li = document.createElement("li"); li.textContent = s; ul.appendChild(li); }
  const gd = $("groups"); gd.innerHTML = "";
  for (const gr of scene.groups) {
    const chip = document.createElement("span"); chip.className = "chip";
    chip.appendChild(document.createTextNode(`${gr.name} (${gr.size})`));
    const b = document.createElement("button"); b.textContent = gr.open ? "fold" : "unfold"; b.addEventListener("click", () => onToggle(gr.id)); chip.appendChild(b);
    gd.appendChild(chip);
  }
  const rd = $("roles"); rd.innerHTML = "";
  for (const r of ALL_ROLES) {
    const l = document.createElement("label"), c = document.createElement("input"); c.type = "checkbox"; c.checked = state.roles.includes(r);
    c.addEventListener("change", () => { state.roles = c.checked ? [...state.roles, r] : state.roles.filter(x => x !== r); onRoles(); });
    l.appendChild(c); l.appendChild(document.createTextNode(" " + r)); rd.appendChild(l);
  }
}

export function syncControls() {
  $("budget").value = state.budget;
  for (const [id, k] of Object.entries(OPTIONS)) $(id).checked = state[k];
  $("nolabels").checked = state.noLabels; $("g").classList.toggle("nolabels", state.noLabels);
  $("raw").checked = state.raw;
  $("direction").value = state.direction;
  $("engine").value = state.engine;
}
