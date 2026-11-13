// The review panel, for diff datasets (nolli diff): the change as a whole, a before /
// changes / after switch, the comments on it (a review agent's verdicts among them: GET
// /api/scene?f=comments, nolli/logic/comments.py), and the changes: the changed boxes and edges
// of the changes preset, a table of it (GET /api/scene?f=table), each named by its id. A comment
// is on a node or edge of the model it was made on; its row shows what it says. A change's row
// expands in place to show its links into the PR. Hovering a row lights what it is about in the
// drawing; clicking it (or j / k) goes there: unfold what it needs, centre on it, flash it and
// open its card.

import { byId } from "./dom.js";
import { inlineHTML } from "./md.js";
import { ALL_ROLES, state } from "./state.js";
import { ORDER, RANK, markHTML } from "./verdicts.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").ReviewItem} ReviewItem
 * @typedef {import("../types/nolli").TableRow} TableRow
 * @typedef {import("../types/nolli").NodeDiff} NodeDiff
 * @typedef {import("../types/nolli").EdgeDiff} EdgeDiff
 * @typedef {import("../types/nolli").FactChange} FactChange
 * @typedef {import("../types/nolli").ListedComment} ListedComment
 * @typedef {{ jump: (id: string) => void, hover: (id: string | null) => void, side: (s: "both" | "base" | "head") => void,
 *             dim: (on: boolean) => void }} ReviewHandlers
 */

const panel = byId("review");
/** @param {unknown} s */
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
/** @param {string | null | undefined} href @param {string} text */
const link = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);
const GLYPH = { added: "+", removed: "−", changed: "~" };
const TITLES = { entity: "Entities", relation: "Relations" };

/** @type {ReviewItem[]} in the panel's order */
let items = [];
/** @type {Map<string, TableRow>} the changes preset's changed boxes and edges, by id */
let rows = new Map();
/** @type {ListedComment[]} worst first */
let comments = [];
/** @type {Set<string>} rows shown expanded */
const open = new Set();

/** The changes listed: those in the roles shown (Files in the sidebar). */
const shownItems = () => items.filter(it => (state.roles || ["source"]).includes(it.role));
/** What j / k step through: the comments on something the model has, then the changes listed. */
export function visibleItems() { return [...comments.filter(c => c.ends.length), ...shownItems()]; }
/** A change or a comment. @param {string} id @returns {ReviewItem | ListedComment | null} */
export const itemById = id => items.find(it => it.id === id) || comments.find(c => c.id === id) || null;

/** The rows of a table of the model (GET /api/scene?f=table); none if it cannot have one (a snapshot has
 * no changes). @param {string} key the model's key @param {string} rest the table's and projection's parameters */
async function fetchTable(key, rest) {
  const res = await fetch(`/api/scene?f=table&k=${encodeURIComponent(key)}&${rest}`);
  return res.ok ? /** @type {TableRow[]} */ ((await res.json()).rows) : [];
}

/** The comments kept on a model (GET /api/scene?f=comments), the verdicts that ask for most attention first, then
 * those without one, each kind in the order they were made. @param {string} key the model's key */
export async function fetchComments(key) {
  const res = await fetch(`/api/scene?f=comments&k=${encodeURIComponent(key)}&p=none`);
  /** @type {ListedComment[]} */
  const cs = res.ok ? (await res.json()).comments : [];
  const rank = (/** @type {ListedComment} */ c) => c.verdict && c.verdict in RANK ? RANK[c.verdict] : -1;
  return cs.map((c, i) => ({ c, i })).sort((a, b) => rank(b.c) - rank(a.c) || a.i - b.i).map(x => x.c);
}

/** Fetch the model's changes (in every role: the panel shows those in the roles shown) and comments; empty for
 * a snapshot. @param {string} key the model's key */
export async function loadItems(key) {
  const changed = "required_overlay=change&overlays=change&p=changes&r=" + ALL_ROLES.join(",");
  const [got, cs] = await Promise.all([fetchTable(key, changed), fetchComments(key)]);
  rows = new Map(got.map(r => [r.id, r]));
  items = listChanges(got);
  comments = cs;
  open.clear();
  return items;
}

/** The boxes around a row's box, outermost first, as far as the table has them. @param {string | null | undefined} id */
function around(id) {
  const out = [];
  for (let p = id ? rows.get(id)?.parent : null; p; p = rows.get(p)?.parent) out.unshift(p);
  return out;
}

const CATEGORIES = ["entity", "relation"];

/** List every entity and relation that changed itself, source first, then entities before relations, then by
 * role, area and title. The server says which changed (required_overlay=change), and which changed themselves
 * (an entity's change has ``own``); those that changed only inside are left out.
 * @param {TableRow[]} got @returns {ReviewItem[]} */
function listChanges(got) {
  /** @type {ReviewItem[]} */
  const out = [];
  for (const r of got) {
    const c = r.change;
    if (!c || (!r.src && !("own" in c))) continue;
    const relation = !!(r.src && r.dst);
    const top = relation ? around(r.src)[0] ?? r.src : around(r.id)[0] ?? r.id;
    const reveal = relation ? [...new Set([...around(r.src), ...around(r.dst)])] : around(r.id);
    const own = relation ? undefined : /** @type {NodeDiff} */ (c).own;
    const lines = own?.git ? ` (+${own.added || 0} −${own.removed || 0})` : "";
    const title = relation ? `${r.src} → ${r.dst}  ${/** @type {EdgeDiff} */ (c).base}→${/** @type {EdgeDiff} */ (c).head}` : `${r.kind} ${r.id}${lines}`;
    out.push({ id: r.id, category: relation ? "relation" : "entity", status: c.status, role: c.role || "source", area: top, reveal, change: c, title });
  }
  const order = (/** @type {ReviewItem} */ it) => [it.role !== "source", CATEGORIES.indexOf(it.category), it.role, it.area, it.title];
  return out.sort((a, b) => {
    const x = order(a), y = order(b);
    for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
    return 0;
  });
}

/** The ids of the drawn boxes that stand for a change's box, or for the ends of a change's edge, when the
 * picture does not draw it itself: its innermost box the picture draws. @param {string} id
 * @param {Set<string>} drawn the ids of the boxes drawn */
export function standIns(id, drawn) {
  const r = rows.get(id);
  if (!r) return [];
  const find = (/** @type {string} */ x) => [x, ...around(x).reverse()].find(y => drawn.has(y));
  return /** @type {string[]} */ ((r.src && r.dst ? [find(r.src), find(r.dst)] : [find(r.id)]).filter(Boolean));
}

/** @param {boolean} on */
export function showPanel(on) { document.body.classList.toggle("reviewing", on); }

/** @param {string} st */
const statusSpan = st => `<span class="st ${esc(st)}">${GLYPH[/** @type {"added" | "removed" | "changed"} */ (st)] || "•"}</span>`;
/** @param {ListedComment} c */
const markSpan = c => { const v = c.verdict || "comment"; return `<span class="vd ${esc(v)}" title="${esc((c.verdict ? v + ": " : "") + c.text)}">${markHTML(v)}</span>`; };

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
  if (it.category === "relation") { const e = /** @type {EdgeDiff} */ (it.change); html += factList(e.changes || [], e.more || 0); }
  /** @type {import("../types/nolli").OwnChange | Record<string, never>} */
  const n = it.category === "relation" ? {} : /** @type {NodeDiff} */ (it.change).own || {};
  const classes = n.classes || [];
  if (classes.length) html += `<div class="dt-sub">classes</div><ul class="facts">${classes.map(c =>
    `<li>${statusSpan(c.status)} ${esc(c.name)} <span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></li>`).join("")}</ul>`;
  if (n.hunks?.length) html += `<div class="hunks">${n.hunks.map(h => link(h.link, `L${h.new[0]}${h.new[1] > 1 ? "–" + (h.new[0] + h.new[1] - 1) : ""}`)).join(" ")}${n.more_hunks ? ` … ${n.more_hunks} more` : ""}</div>`;
  const where = n.link ? n.link : it.category === "relation" ? /** @type {EdgeDiff} */ (it.change).changes?.[0]?.link : null;
  html += `<div class="muted">${where ? link(where, "↗ diff") + " · " : ""}${esc(it.role)} · in ${esc(it.area)}</div>`;
  return `<div class="rv-more">${html}</div>`;
}

/** Draw the panel for a scene. @param {Scene} scene @param {ReviewHandlers} h */
export function renderPanel(scene, h) {
  const d = scene.diff;
  if (!d) { showPanel(false); return; }
  showPanel(true);
  const shown = shownItems();
  /** @type {Record<string, number>} */
  const counts = {};
  for (const c of comments) if (c.verdict) counts[c.verdict] = (counts[c.verdict] || 0) + 1;
  const sideBtn = (/** @type {"base" | "both" | "head"} */ s, /** @type {string} */ label, /** @type {string} */ key) =>
    `<button data-side="${s}" class="${state.side === s ? "on" : ""}" title="key ${key}">${label}</button>`;
  let html = `<div class="rv-head">
    <div class="rv-title">${esc(d.title || "Change")}</div>
    <div class="rv-sub">${link(d.link, d.pr ? `PR #${d.pr}` : "compare")} · ${esc(d.base_short)} → ${esc(d.head_short)}
      · ${d.files_changed} files <span class="plus">+${d.added}</span> <span class="minus">−${d.removed}</span></div>
  </div><div class="rv-list">`;
  // the comments: their heading counts the verdicts among them
  const tally = ORDER.filter(v => counts[v]).map(v => `<span class="vd ${v}" title="${v}">${markHTML(v)}</span>${counts[v]}`).join(" ");
  if (comments.length) html += `<div class="rv-group rr-group"><span>Comments</span>${tally ? `<span class="rr-tally">${tally}</span>` : ""}</div>`;
  for (const c of comments) {
    const cur = state.item === c.id ? " current" : "", off = c.ends.length ? "" : " off";
    html += `<div class="rv-row rv-verdict v-${esc(c.verdict || "comment")}${cur}${off}" data-item="${esc(c.id)}"><div class="rv-item">${markSpan(c)}<span class="t"><b>${esc(c.label)}</b>${c.ends.length ? "" : ` <span class="muted">(not in this model)</span>`}<br>${inlineHTML(c.text)}</span></div></div>`;
  }
  let last = "";
  for (const it of shown) {
    if (it.category !== last) { html += `<div class="rv-group">${TITLES[it.category]}</div>`; last = it.category; }
    const cur = state.item === it.id ? " current" : "", isOpen = open.has(it.id);
    const role = it.role !== "source" ? `<span class="role">${esc(it.role)}</span>` : "";
    html += `<div class="rv-row${cur}" data-item="${esc(it.id)}"><div class="rv-item"><button class="tw" data-open="${esc(it.id)}" title="details">${isOpen ? "▾" : "▸"}</button>${statusSpan(it.status)}<span class="t">${esc(it.title)}</span>${role}</div>${isOpen ? details(it) : ""}</div>`;
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
