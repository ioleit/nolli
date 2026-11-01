// The review panel, for diff datasets (nolli diff): the change as a whole, a before /
// changes / after switch, the review agent's verdicts (verdicts.json; where they came from
// and running a review: reviewer.js), and
// the numbered changes. A verdict is on a node or edge of the review diagram; its row shows
// the reason. A change's row expands in place to show its links into the PR. Hovering a row
// lights what it is about in the drawing; clicking it (or j / k) goes there: unfold what it
// needs, centre on it, flash it and open its card.

import { byId } from "./dom.js";
import { inlineHTML } from "./md.js";
import { state } from "./state.js";
import { ORDER, RANK, markHTML } from "./verdicts.js";
import { draw as drawReview, follow, setDiff } from "./reviewer.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").ReviewItem} ReviewItem
 * @typedef {import("../types/nolli").FactChange} FactChange
 * @typedef {import("../types/nolli").ReviewVerdict} ReviewVerdict
 * @typedef {{ jump: (id: string) => void, hover: (id: string | null) => void, side: (s: "both" | "base" | "head") => void,
 *             dim: (on: boolean) => void, reload: () => void }} ReviewHandlers
 */

const panel = byId("review");
/** @param {unknown} s */
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
/** @param {string | null | undefined} href @param {string} text */
const link = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);
const GLYPH = { added: "+", removed: "−", changed: "~" };
const TITLES = { component: "Components added or removed", dependency: "Dependencies between modules", content: "Edited files" };

/** @type {ReviewItem[]} */
let items = [];
/** @type {ReviewVerdict[]} worst first */
let verdicts = [];
/** @type {ReviewHandlers | null} */
let handlers = null;
/** @type {Set<string>} rows shown expanded */
const open = new Set();

/** @type {Record<import("../types/nolli").Verdict, number>} */

/** The changes listed: those in the roles shown (Files in the sidebar). */
const shownItems = () => items.filter(it => (state.roles || ["source"]).includes(it.role));
/** What j / k step through: the verdicts on something in the diagram, then the changes listed. */
export function visibleItems() { return [...verdicts.filter(v => v.ends.length), ...shownItems()]; }
/** A change or a verdict. @param {string} id @returns {ReviewItem | ReviewVerdict | null} */
export const itemById = id => items.find(it => it.id === id) || verdicts.find(v => v.id === id) || null;

/** Fetch the dataset's changes and verdicts; empty for a plain dataset. @param {string} name */
export async function loadItems(name) {
  const res = await fetch("/api/changes?d=" + encodeURIComponent(name));
  const body = res.ok ? await res.json() : { items: [], verdicts: [] };
  items = body.items;
  verdicts = [.../** @type {ReviewVerdict[]} */ (body.verdicts)].sort((a, b) => RANK[b.verdict] - RANK[a.verdict] || +a.id.slice(1) - +b.id.slice(1));
  open.clear();
  if (name) follow(name, () => handlers?.reload());  // not awaited: the panel draws the block when it comes
  return items;
}

/** @param {boolean} on */
export function showPanel(on) { document.body.classList.toggle("reviewing", on); }

/** @param {string} st */
const statusSpan = st => `<span class="st ${esc(st)}">${GLYPH[/** @type {"added" | "removed" | "changed"} */ (st)] || "•"}</span>`;
/** @param {{ verdict: string, reason: string } | null | undefined} v */
const verdictSpan = v => v ? `<span class="vd ${esc(v.verdict)}" title="${esc(v.verdict + ": " + v.reason)}">${markHTML(v.verdict)}</span>` : "";

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
  const shown = shownItems();
  /** @type {Record<string, number>} */
  const counts = {};
  for (const v of verdicts) counts[v.verdict] = (counts[v.verdict] || 0) + 1;
  const sideBtn = (/** @type {"base" | "both" | "head"} */ s, /** @type {string} */ label, /** @type {string} */ key) =>
    `<button data-side="${s}" class="${state.side === s ? "on" : ""}" title="key ${key}">${label}</button>`;
  let html = `<div class="rv-head">
    <div class="rv-title">${esc(d.title || "Change")}</div>
    <div class="rv-sub">${link(d.link, d.pr ? `PR #${d.pr}` : "compare")} · ${esc(d.base_short)} → ${esc(d.head_short)}
      · ${d.files_changed} files <span class="plus">+${d.added}</span> <span class="minus">−${d.removed}</span></div>
    ${verdicts.length ? `<div class="rv-verdicts">verdicts: ${ORDER.filter(v => counts[v]).map(v => `<span class="vd ${v}" title="${v}">${markHTML(v)}</span> ${counts[v]}`).join(" ")}</div>` : ""}
    <div id="rv-review" class="rr"></div>
  </div><div class="rv-list">`;
  if (verdicts.length) html += `<div class="rv-group">Review</div>`;
  for (const v of verdicts) {
    const cur = state.item === v.id ? " current" : "", off = v.ends.length ? "" : " off";
    html += `<div class="rv-row rv-verdict v-${esc(v.verdict)}${cur}${off}" data-item="${esc(v.id)}"><div class="rv-item">${verdictSpan(v)}<span class="t"><b>${esc(v.label)}</b>${v.ends.length ? "" : ` <span class="muted">(not in the diagram)</span>`}<br>${inlineHTML(v.reason)}</span></div></div>`;
  }
  let last = "";
  for (const it of shown) {
    if (it.category !== last) { html += `<div class="rv-group">${TITLES[it.category]}</div>`; last = it.category; }
    const cur = state.item === it.id ? " current" : "", isOpen = open.has(it.id);
    const role = it.role !== "source" ? `<span class="role">${esc(it.role)}</span>` : "";
    html += `<div class="rv-row${cur}" data-item="${esc(it.id)}"><div class="rv-item"><button class="tw" data-open="${esc(it.id)}" title="details">${isOpen ? "▾" : "▸"}</button><span class="id">${esc(it.id)}</span>${statusSpan(it.status)}<span class="t">${esc(shortTitle(it))}</span>${role}</div>${isOpen ? details(it) : ""}</div>`;
  }
  if (!shown.length) html += `<div class="rv-empty">no changes${items.length ? " in the roles shown; show more under Files in the sidebar" : ""}</div>`;
  // the controls, docked under the list: one row of buttons, one of keys
  const tog = (/** @type {string} */ id, /** @type {boolean} */ on, /** @type {string} */ label, /** @type {string} */ help) =>
    `<button class="tog" id="${id}" aria-pressed="${on}" title="${help}">${label}</button>`;
  html += `</div><div class="rv-bar">
    <div class="rv-bar-row"><div class="seg">${sideBtn("base", "before", "1")}${sideBtn("both", "changes", "2")}${sideBtn("head", "after", "3")}</div>
      ${tog("rv-dim", state.dim, "fade unchanged", "fade what did not change")}</div>
    <div class="rv-keys">keys: j k next / previous · 1 2 3 before / changes / after · f fit</div>
  </div>`;
  const keepScroll = panel.querySelector(".rv-list")?.scrollTop;
  panel.innerHTML = html;
  setDiff(d);
  drawReview();
  const list = panel.querySelector(".rv-list");
  if (list && keepScroll !== undefined) list.scrollTop = keepScroll;
  panel.querySelectorAll("[data-side]").forEach(b => b.addEventListener("click", () => h.side(/** @type {"both" | "base" | "head"} */ (/** @type {HTMLElement} */ (b).dataset.side))));
  panel.querySelectorAll(".rv-row").forEach(r => {
    const id = /** @type {string} */ (/** @type {HTMLElement} */ (r).dataset.item);
    r.querySelector(".rv-item")?.addEventListener("click", ev => {
      if (/** @type {Element} */ (ev.target).closest("[data-open]")) { open.has(id) ? open.delete(id) : open.add(id); renderPanel(scene, h); return; }
      if (!r.classList.contains("off")) h.jump(id);
    });
    r.addEventListener("mouseenter", () => h.hover(id));
    r.addEventListener("mouseleave", () => h.hover(null));
  });
  byId("rv-dim").addEventListener("click", ev => { h.dim(!state.dim); /** @type {HTMLElement} */ (ev.currentTarget).setAttribute("aria-pressed", String(state.dim)); });
}

/** Keep the current row in view after a jump. */
export function scrollToCurrent() { panel.querySelector(".rv-row.current")?.scrollIntoView({ block: "nearest" }); }
