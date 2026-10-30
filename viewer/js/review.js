// The review panel, for diff datasets (nolli diff): the change as a whole, a before /
// changes / after switch, the numbered review items, and details for whatever is selected
// (an item, a box or an edge) with links into the PR. Verdicts from a review agent
// (verdicts.json, or loaded here) show on the items and in the drawing.

import { byId } from "./dom.js";
import { state } from "./state.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").ReviewItem} ReviewItem
 * @typedef {import("../types/nolli").Details} Details
 * @typedef {import("../types/nolli").FactChange} FactChange
 * @typedef {import("../types/nolli").SceneEdge} SceneEdge
 * @typedef {{ selectItem: (id: string) => void, select: (what: string) => void, side: (s: "both" | "base" | "head") => void,
 *             dim: (on: boolean) => void, reload: () => void, roles: (all: boolean) => void }} ReviewHandlers
 */

const panel = byId("review");
/** @param {unknown} s */
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
/** @param {string | null | undefined} href @param {string} text */
const link = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);
const GLYPH = { added: "+", removed: "−", changed: "~" };
const VGLYPH = { fine: "✓", question: "?", problem: "!" };
const TITLES = { component: "Components added or removed", dependency: "Dependencies between modules", content: "Edited files" };

/** @type {ReviewItem[]} */
let items = [];
let allRoles = false;
/** @type {ReviewHandlers | null} */
let handlers = null;

/** Items shown in the list: source changes, or everything. */
export function visibleItems() { return items.filter(it => allRoles || it.role === "source"); }
/** @param {string} id */
export const itemById = id => items.find(it => it.id === id) || null;

/** Fetch the dataset's items (with verdicts); empty for a plain dataset. @param {string} name */
export async function loadItems(name) {
  const res = await fetch("/api/changes?d=" + encodeURIComponent(name));
  items = res.ok ? (await res.json()).items : [];
  return items;
}

/** @param {boolean} on */
export function showPanel(on) { document.body.classList.toggle("reviewing", on); }

/** @param {string} st */
const statusSpan = st => `<span class="st ${esc(st)}">${GLYPH[/** @type {"added" | "removed" | "changed"} */ (st)] || "•"}</span>`;
/** @param {{ verdict: string, reason: string } | null | undefined} v */
const verdictSpan = v => v ? `<span class="vd ${esc(v.verdict)}" title="${esc(v.verdict + ": " + v.reason)}">${VGLYPH[/** @type {"fine" | "question" | "problem"} */ (v.verdict)]}</span>` : "";

/**
 * Draw the panel for a scene. @param {Scene} scene @param {ReviewHandlers} h
 */
export function renderPanel(scene, h) {
  handlers = h;
  const d = scene.diff;
  if (!d) { showPanel(false); return; }
  showPanel(true);
  const shown = visibleItems();
  const counts = { problem: 0, question: 0, fine: 0 };
  for (const it of items) if (it.verdict) counts[it.verdict.verdict]++;
  const hiddenN = items.length - shown.length;
  const sideBtn = (/** @type {"base" | "both" | "head"} */ s, /** @type {string} */ label, /** @type {string} */ key) =>
    `<button data-side="${s}" class="${state.side === s ? "on" : ""}" title="key ${key}">${label}</button>`;
  let html = `<div class="rv-head">
    <div class="rv-title">${esc(d.title || "Change")}</div>
    <div class="rv-sub">${link(d.link, d.pr ? `PR #${d.pr}` : "compare")} · ${esc(d.base_short)} → ${esc(d.head_short)}
      · ${d.files_changed} files <span class="plus">+${d.added}</span> <span class="minus">−${d.removed}</span></div>
    <div class="seg">${sideBtn("base", "before", "1")}${sideBtn("both", "changes", "2")}${sideBtn("head", "after", "3")}</div>
    <label><input type="checkbox" id="rv-dim" ${state.dim ? "checked" : ""}> fade what did not change</label>
    <label><input type="checkbox" id="rv-all" ${allRoles ? "checked" : ""}> include tests, docs, config, data${hiddenN && !allRoles ? ` (${hiddenN} more)` : ""}</label>
    <div class="rv-verdicts">${counts.problem + counts.question + counts.fine
      ? `verdicts: <span class="vd problem">!</span> ${counts.problem} <span class="vd question">?</span> ${counts.question} <span class="vd fine">✓</span> ${counts.fine}`
      : "no verdicts yet"} · <a href="/data/${esc(state.name)}/review.md" target="_blank">review.md</a>
      · <label class="file">load verdicts…<input type="file" id="rv-file" accept=".json,application/json"></label></div>
  </div><div class="rv-keys">j / k next / previous · 1 2 3 before / changes / after · f fit · esc clear</div><div class="rv-list">`;
  let last = "";
  for (const it of shown) {
    if (it.category !== last) { html += `<div class="rv-group">${TITLES[it.category]}</div>`; last = it.category; }
    const sel = state.sel === "item:" + it.id ? " sel" : "";
    const role = it.role !== "source" ? `<span class="role">${esc(it.role)}</span>` : "";
    html += `<div class="rv-item${sel}" data-item="${esc(it.id)}"><span class="id">${esc(it.id)}</span>${statusSpan(it.status)}<span class="t">${esc(shortTitle(it))}</span>${role}${verdictSpan(it.verdict)}</div>`;
  }
  if (!shown.length) html += `<div class="rv-empty">no changes${hiddenN ? " in source code; tick the box above for the rest" : ""}</div>`;
  html += `</div><div class="rv-details" id="rv-details"></div>`;
  panel.innerHTML = html;
  panel.querySelectorAll("[data-side]").forEach(b => b.addEventListener("click", () => h.side(/** @type {"both" | "base" | "head"} */ (/** @type {HTMLElement} */ (b).dataset.side))));
  panel.querySelectorAll("[data-item]").forEach(r => r.addEventListener("click", () => h.selectItem(/** @type {string} */ (/** @type {HTMLElement} */ (r).dataset.item))));
  /** @type {HTMLInputElement} */ (byId("rv-dim")).addEventListener("change", ev => h.dim(/** @type {HTMLInputElement} */ (ev.target).checked));
  /** @type {HTMLInputElement} */ (byId("rv-all")).addEventListener("change", ev => { allRoles = /** @type {HTMLInputElement} */ (ev.target).checked; h.roles(allRoles); });
  /** @type {HTMLInputElement} */ (byId("rv-file")).addEventListener("change", ev => uploadVerdicts(/** @type {HTMLInputElement} */ (ev.target)));
  const selRow = panel.querySelector(".rv-item.sel");
  if (selRow) selRow.scrollIntoView({ block: "nearest" });
}

/** The title without the parts the group heading already says. @param {ReviewItem} it */
function shortTitle(it) {
  if (it.category === "dependency") return it.title.replace(/: (new dependency|dependency removed|dependency changed)$/, "") + (it.base !== undefined ? `  ${it.base}→${it.head}` : "");
  if (it.category === "content") return it.title.replace(/^edited /, "");
  return it.title.replace(/^(new|removed) /, "");
}

/** @param {HTMLInputElement} input */
async function uploadVerdicts(input) {
  const f = input.files && input.files[0];
  if (!f || !state.name) return;
  const res = await fetch("/api/verdicts?d=" + encodeURIComponent(state.name), { method: "POST", body: await f.text() });
  const body = await res.json();
  if (!res.ok) { alert("verdicts not loaded: " + body.error); return; }
  handlers?.reload();
}

// ---------- details ----------

/** @param {FactChange[]} cs @param {number} more */
function factList(cs, more) {
  if (!cs.length) return "";
  let html = `<ul class="facts">`;
  for (const c of cs) {
    const where = c.path ? (c.line ? `${c.path}:${c.line}` : c.path) : "";
    html += `<li>${statusSpan(c.status)} ${esc(c.src.replace(/^(fs|py):/, ""))} → ${esc(c.dst.replace(/^(fs|py):/, ""))}
      <span class="muted">${c.kind === "use" ? "" : esc(c.kind) + " "}${esc(c.subkind)} ${c.base}→${c.head}</span> ${c.link ? link(c.link, "↗ " + where) : ""}</li>`;
  }
  return html + (more ? `<li class="muted">… ${more} more</li>` : "") + "</ul>";
}

/** Details of an item. @param {ReviewItem} it */
export function showItem(it) {
  const el = byId("rv-details");
  let html = `<div class="dt-title">${esc(it.id)} ${statusSpan(it.status)} ${esc(it.title)} ${it.link ? link(it.link, "↗ diff") : ""}</div>`;
  if (it.verdict) html += `<div class="dt-verdict ${esc(it.verdict.verdict)}">${verdictSpan(it.verdict)} <b>${esc(it.verdict.verdict)}</b>: ${esc(it.verdict.reason)}</div>`;
  if (it.category === "dependency") {
    html += `<div>${it.base} → ${it.head} references</div>`;
    if (it.targets?.length) html += `<ul class="facts">${it.targets.map(t => `<li>${statusSpan(t[0] === "+" ? "added" : t[0] === "−" ? "removed" : "changed")} ${esc(t.slice(1))}</li>`).join("")}</ul>`;
  }
  if (it.classes?.length) html += `<div class="dt-sub">classes</div><ul class="facts">${it.classes.map(c => typeof c === "string" ? `<li>${esc(c)}</li>`
    : `<li>${esc(c.name)} <span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></li>`).join("")}</ul>`;
  if (it.category === "content") html += `<div><span class="plus">+${it.added}</span> <span class="minus">−${it.removed}</span> lines</div>`;
  html += `<div class="muted dt-hint">${it.entities.length > 1 && it.category !== "dependency" ? it.entities.length + " entities · " : ""}${esc(it.role)} · in ${esc(it.area.replace(/^fs:/, ""))}</div>`;
  el.innerHTML = html;
}

/** Details of a box (GET /api/node). @param {string} name @param {string} id */
export async function showNode(name, id) {
  const el = byId("rv-details");
  el.innerHTML = `<div class="muted">loading…</div>`;
  const res = await fetch(`/api/node?d=${encodeURIComponent(name)}&id=${encodeURIComponent(id)}`);
  if (!res.ok) { el.innerHTML = `<div class="muted">no details for ${esc(id)}</div>`; return; }
  /** @type {Details} */
  const d = await res.json();
  if (state.sel !== "node:" + id) return;  // another selection came in meanwhile
  const st = d.diff;
  let html = `<div class="dt-title">${st ? statusSpan(st.status) : ""} ${esc(d.path || d.name)} ${d.link ? link(d.link, "↗ diff") : ""}</div>`;
  if (!st) { el.innerHTML = html + `<div class="muted">nothing changed here</div>`; return; }
  html += `<div>${st.files ? `${st.files} files ` : ""}<span class="plus">+${st.added}</span> <span class="minus">−${st.removed}</span>${st.new ? ` · ${st.new} new` : ""}${st.gone ? ` · ${st.gone} removed` : ""}${st.old_path ? ` · renamed from ${esc(st.old_path)}` : ""}</div>`;
  if (d.files.length) {
    html += `<div class="dt-sub">files</div><ul class="files">`;
    for (const f of d.files) {
      html += `<li><span class="git ${esc(f.git)}">${esc(f.git)}</span> ${link(f.link, f.path)} <span class="plus">+${f.added}</span> <span class="minus">−${f.removed}</span>${f.blob ? " " + link(f.blob, "file") : ""}`;
      if (f.hunks.length > 1 || (f.hunks.length === 1 && f.git === "M")) html += `<div class="hunks">${f.hunks.map(hk => link(hk.link, `L${hk.new[0]}${hk.new[1] > 1 ? "–" + (hk.new[0] + hk.new[1] - 1) : ""}`)).join(" ")}${f.more_hunks ? ` … ${f.more_hunks} more` : ""}</div>`;
      html += `</li>`;
    }
    html += `</ul>${d.more_files ? `<div class="muted">… ${d.more_files} more files</div>` : ""}`;
  }
  if (d.classes.length) html += `<div class="dt-sub">classes with changed lines</div><ul class="facts">${d.classes.map(c =>
    `<li>${link(c.link, c.name)} <span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span> <span class="muted">${esc(c.path)}:${c.lines[0]}–${c.lines[1]}</span></li>`).join("")}</ul>`;
  if (d.entities.length) html += `<div class="dt-sub">added or removed</div><ul class="facts">${d.entities.map(x =>
    `<li>${statusSpan(x.status)} <span class="muted">${esc(x.kind)}</span> ${link(x.link, x.name)}</li>`).join("")}</ul>${d.more_entities ? `<div class="muted">… ${d.more_entities} more</div>` : ""}`;
  if (d.dependencies.length) html += `<div class="dt-sub">changed dependencies in and out</div>` + factList(d.dependencies, d.more_dependencies);
  el.innerHTML = html;
}

/** Details of an edge, from the scene. @param {SceneEdge} e */
export function showEdge(e) {
  const el = byId("rv-details");
  const name = (/** @type {string} */ x) => x.replace(/^(fs|py|body):/, "");
  let html = `<div class="dt-title">${e.diff ? statusSpan(e.diff.status) : ""} ${esc(name(e.src))} → ${esc(name(e.dst))}</div>`;
  if (!e.diff) { el.innerHTML = html + `<div class="muted">${e.count} references, unchanged</div>`; return; }
  html += `<div>${e.diff.base} → ${e.diff.head} references · ${e.diff.facts.length} facts changed underneath${e.items?.length ? " · items " + e.items.map(i => `<a href="#" data-goto="${esc(i)}">${esc(i)}</a>`).join(" ") : ""}</div>`;
  html += factList(e.diff.changes, e.diff.more);
  el.innerHTML = html;
  el.querySelectorAll("[data-goto]").forEach(a => a.addEventListener("click", ev => { ev.preventDefault(); handlers?.selectItem(/** @type {string} */ (/** @type {HTMLElement} */ (a).dataset.goto)); }));
}

export function clearDetails() { const el = document.getElementById("rv-details"); if (el) el.innerHTML = `<div class="muted">select a change, a box or an edge</div>`; }
