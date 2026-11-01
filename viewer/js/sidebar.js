// The sidebar: dataset meta, the Show filter (groups, files by role), residue, and the controls' values.

import { byId, input, select } from "./dom.js";
import { ALL_ROLES, state } from "./state.js";

/** @typedef {import("../types/nolli").Scene} Scene */

/** @type {Record<string, string>} what showing a group means, by its namespace (nolli/model/address.py); the server names the groups */
const GROUP_HELP = {
  api: "What this repo serves for others to call: one box per app, unfolding along its paths to the operations. Calls go into it, and from it to the handlers that realise them. Hidden: the callers' hover text counts their calls.",
  config: "Settings the code reads at runtime (environment variables), as one box you can unfold. Readers point into it, and from it to the services that set them. Hidden: the readers' hover text counts what they read.",
  runtime: "What runs: Compose services and the hosts outside they reach, as one box you can unfold. Hidden: the hover text of the code they touch says so.",
  pkg: "Artifacts: third-party packages and the images built here, as one box you can unfold. Hidden: the importers' hover text names them.",
};

/** @type {Record<string, string>} how nolli/extractors/fs.py assigns each role */
const ROLE_HELP = {
  source: "Code in a language nolli knows.",
  mixed: "Folders holding source next to files of other roles.",
  test: "Files in a test folder or named like a test.",
  docs: "Markdown, reStructuredText, plain text.",
  config: "Config formats, lock files, Dockerfiles, Makefiles, tool configs, dot-files and dot-folders.",
  markup: "HTML and stylesheets.",
  data: "CSV, JSON Lines, Parquet, XML, GeoJSON.",
  asset: "Images, audio, video, fonts.",
  other: "Anything else.",
};
const SOURCE_ONLY = ["source", "mixed"];

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
    toggle(gd, gr.name, String(gr.size), shown.includes(gr.id), GROUP_HELP[gr.namespace] || `${gr.name}: one box you can unfold. Hidden: the hover text of the boxes that use it says so.`, on => {
      const now = state.groups || shown; state.groups = on ? [...now, gr.id] : now.filter(x => x !== gr.id); onFilter();
    });
  }
  if (!scene.groups.length) gd.innerHTML = `<span class="muted">none in this repository</span>`;

  // roles the repository has, in their usual order; a shown role it lacks stays in the state
  const rd = byId("roles"); rd.innerHTML = "";
  const roles = state.roles || [];
  for (const r of scene.roles) {
    if (!r.files && !r.dirs) continue;
    const n = r.files ? String(r.files) : `${r.dirs} dir${r.dirs === 1 ? "" : "s"}`;
    const help = `${ROLE_HELP[r.id] || r.id} ${r.files} file${r.files === 1 ? "" : "s"}, ${r.dirs} folder${r.dirs === 1 ? "" : "s"}. Hidden: left out, and so are their edges (counted under Residue).`;
    toggle(rd, r.id, n, roles.includes(r.id), help, on => {
      const now = state.roles || []; state.roles = on ? [...now, r.id] : now.filter(x => x !== r.id); onFilter();
    });
  }
  const set = (/** @type {string[]} */ rs) => () => { state.roles = rs; onFilter(); };
  const same = (/** @type {string[]} */ a, /** @type {string[]} */ b) => a.length === b.length && a.every(x => b.includes(x));
  quick(byId("rolequick"), [["source only", SOURCE_ONLY, same(roles, SOURCE_ONLY)], ["all", ALL_ROLES, same(roles, ALL_ROLES)]].map(([t, rs, on]) => ({ text: /** @type {string} */ (t), on: /** @type {boolean} */ (on), act: set(/** @type {string[]} */ (rs)) })));
}

/**
 * A toggle chip: name, a muted count, pressed when shown.
 * @param {HTMLElement} parent @param {string} text @param {string} note @param {boolean} on @param {string} help @param {(on: boolean) => void} change
 */
function toggle(parent, text, note, on, help, change) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "tog"; b.dataset.help = help;
  b.setAttribute("aria-pressed", String(on));
  b.append(text);
  const s = document.createElement("span"); s.className = "n"; s.textContent = note; b.append(s);
  b.addEventListener("click", () => change(!on));
  parent.appendChild(b);
}

/** Links that set the filter at once; the one in effect is plain text. @param {HTMLElement} parent @param {{ text: string, on: boolean, act: () => void }[]} items */
function quick(parent, items) {
  parent.innerHTML = "";
  items.forEach((it, i) => {
    if (i) parent.append(" · ");
    if (it.on) { const s = document.createElement("span"); s.textContent = it.text; parent.append(s); return; }
    const a = document.createElement("button"); a.type = "button"; a.className = "link"; a.textContent = it.text;
    a.addEventListener("click", it.act); parent.append(a);
  });
}

export function syncControls() {
  input("budget").value = String(state.budget);
  input("utilities").value = String(state.utilities ?? "");
  select("theme").value = state.theme;
  for (const b of document.querySelectorAll("#views [data-view]")) b.setAttribute("aria-selected", String(/** @type {HTMLElement} */ (b).dataset.view === state.view));
}
