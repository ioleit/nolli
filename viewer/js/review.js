// The review panel, for diff datasets (nolli diff): the change as a whole, a before /
// changes / after switch, and the numbered review items. A row expands in place to show
// the item's links into the PR. Hovering a row lights the item in the drawing; clicking a
// row (or j / k) jumps to it: unfold what it needs, centre on it, flash it. Verdicts from a
// review agent (verdicts.json, or loaded here) show on the rows and in the drawing.

import { byId } from "./dom.js";
import { state } from "./state.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").ReviewItem} ReviewItem
 * @typedef {import("../types/nolli").FactChange} FactChange
 * @typedef {{ jump: (id: string) => void, hover: (id: string | null) => void, side: (s: "both" | "base" | "head") => void,
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
/** @type {Set<string>} rows shown expanded */
const open = new Set();

/** Items shown in the list: source changes, or everything. */
export function visibleItems() { return items.filter(it => allRoles || it.role === "source"); }
/** @param {string} id */
export const itemById = id => items.find(it => it.id === id) || null;

/** Fetch the dataset's items (with verdicts); empty for a plain dataset. @param {string} name */
export async function loadItems(name) {
  const res = await fetch("/api/changes?d=" + encodeURIComponent(name));
  items = res.ok ? (await res.json()).items : [];
  open.clear();
  return items;
}

/** @param {boolean} on */
export function showPanel(on) { document.body.classList.toggle("reviewing", on); }

/** @param {string} st */
const statusSpan = st => `<span class="st ${esc(st)}">${GLYPH[/** @type {"added" | "removed" | "changed"} */ (st)] || "•"}</span>`;
/** @param {{ verdict: string, reason: string } | null | undefined} v */
const verdictSpan = v => v ? `<span class="vd ${esc(v.verdict)}" title="${esc(v.verdict + ": " + v.reason)}">${VGLYPH[/** @type {"fine" | "question" | "problem"} */ (v.verdict)]}</span>` : "";

/** The title without the parts the group heading already says. @param {ReviewItem} it */
function shortTitle(it) {
  if (it.category === "dependency") return it.title.replace(/: (new dependency|dependency removed|dependency changed)$/, "") + (it.base !== undefined ? `  ${it.base}→${it.head}` : "");
  if (it.category === "content") return it.title.replace(/^edited /, "");
  return it.title.replace(/^(new|removed) /, "");
}

/** @param {FactChange[]} cs @param {number} more */
function factList(cs, more) {
  if (!cs.length) return "";
  const name = (/** @type {string} */ x) => x.replace(/^(fs|py):/, "");
  return `<ul class="facts">${cs.map(c => `<li>${statusSpan(c.status)} ${c.link ? link(c.link, name(c.dst)) : esc(name(c.dst))}
    <span class="muted">${c.base}→${c.head}${c.line ? ` · line ${c.line}` : ""}</span></li>`).join("")}${more ? `<li class="muted">… ${more} more</li>` : ""}</ul>`;
}

/** An item's details, shown under its row. @param {ReviewItem} it */
function details(it) {
  let html = "";
  if (it.verdict) html += `<div class="dt-verdict">${verdictSpan(it.verdict)} <b>${esc(it.verdict.verdict)}</b>: ${esc(it.verdict.reason)}</div>`;
  if (it.category === "dependency") html += factList(it.changes || [], it.more || 0);
  if (it.classes?.length) html += `<div class="dt-sub">classes</div><ul class="facts">${it.classes.map(c => typeof c === "string" ? `<li>${esc(c)}</li>`
    : `<li>${esc(c.name)} <span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></li>`).join("")}</ul>`;
  if (it.category === "content" && it.hunks?.length) html += `<div class="hunks">${it.hunks.map(h => link(h.link, `L${h.new[0]}${h.new[1] > 1 ? "–" + (h.new[0] + h.new[1] - 1) : ""}`)).join(" ")}${it.more_hunks ? ` … ${it.more_hunks} more` : ""}</div>`;
  html += `<div class="muted">${it.link ? link(it.link, "↗ diff") + " · " : ""}${esc(it.role)} · in ${esc(it.area.replace(/^fs:/, ""))}</div>`;
  return `<div class="rv-more">${html}</div>`;
}

/** Draw the panel for a scene. @param {Scene} scene @param {ReviewHandlers} h */
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
  </div><div class="rv-keys">hover a change to light it · click or j / k to go to it · ▸ details · 1 2 3 before / changes / after · f fit</div><div class="rv-list">`;
  let last = "";
  for (const it of shown) {
    if (it.category !== last) { html += `<div class="rv-group">${TITLES[it.category]}</div>`; last = it.category; }
    const cur = state.item === it.id ? " current" : "", isOpen = open.has(it.id);
    const role = it.role !== "source" ? `<span class="role">${esc(it.role)}</span>` : "";
    html += `<div class="rv-row${cur}" data-item="${esc(it.id)}"><div class="rv-item"><button class="tw" data-open="${esc(it.id)}" title="details">${isOpen ? "▾" : "▸"}</button><span class="id">${esc(it.id)}</span>${statusSpan(it.status)}<span class="t">${esc(shortTitle(it))}</span>${role}${verdictSpan(it.verdict)}</div>${isOpen ? details(it) : ""}</div>`;
  }
  if (!shown.length) html += `<div class="rv-empty">no changes${hiddenN ? " in source code; tick the box above for the rest" : ""}</div>`;
  html += `</div>`;
  const keepScroll = panel.querySelector(".rv-list")?.scrollTop;
  panel.innerHTML = html;
  const list = panel.querySelector(".rv-list");
  if (list && keepScroll !== undefined) list.scrollTop = keepScroll;
  panel.querySelectorAll("[data-side]").forEach(b => b.addEventListener("click", () => h.side(/** @type {"both" | "base" | "head"} */ (/** @type {HTMLElement} */ (b).dataset.side))));
  panel.querySelectorAll(".rv-row").forEach(r => {
    const id = /** @type {string} */ (/** @type {HTMLElement} */ (r).dataset.item);
    r.querySelector(".rv-item")?.addEventListener("click", ev => {
      if (/** @type {Element} */ (ev.target).closest("[data-open]")) { open.has(id) ? open.delete(id) : open.add(id); renderPanel(scene, h); return; }
      h.jump(id);
    });
    r.addEventListener("mouseenter", () => h.hover(id));
    r.addEventListener("mouseleave", () => h.hover(null));
  });
  /** @type {HTMLInputElement} */ (byId("rv-dim")).addEventListener("change", ev => h.dim(/** @type {HTMLInputElement} */ (ev.target).checked));
  /** @type {HTMLInputElement} */ (byId("rv-all")).addEventListener("change", ev => { allRoles = /** @type {HTMLInputElement} */ (ev.target).checked; h.roles(allRoles); });
  /** @type {HTMLInputElement} */ (byId("rv-file")).addEventListener("change", ev => uploadVerdicts(/** @type {HTMLInputElement} */ (ev.target)));
}

/** Keep the current row in view after a jump. */
export function scrollToCurrent() { panel.querySelector(".rv-row.current")?.scrollIntoView({ block: "nearest" }); }

/** @param {HTMLInputElement} input */
async function uploadVerdicts(input) {
  const f = input.files && input.files[0];
  if (!f || !state.name) return;
  const res = await fetch("/api/verdicts?d=" + encodeURIComponent(state.name), { method: "POST", body: await f.text() });
  const body = await res.json();
  if (!res.ok) { alert("verdicts not loaded: " + body.error); return; }
  handlers?.reload();
}
