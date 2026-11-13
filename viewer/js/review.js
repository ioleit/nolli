// The review panel, for diff datasets (nolli diff): the change as a whole, a before /
// changes / after switch, the review agent's verdicts (verdicts.json; where they came from
// and running a review: reviewer.js), and the changes: the changed boxes and edges of the
// changes preset, a table of it (GET /api/scene?f=table), each named by its id. A verdict is on a node or
// edge of the review diagram; its row shows the reason. A change's row expands in place to
// show its links into the PR. Hovering a row lights what it is about in the drawing; clicking
// it (or j / k) goes there: unfold what it needs, centre on it, flash it and open its card.

import { byId } from "./dom.js";
import { inlineHTML } from "./md.js";
import { ALL_ROLES, state } from "./state.js";
import { ORDER, RANK, markHTML } from "./verdicts.js";
import { draw as drawReview, follow, setDiff } from "./reviewer.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("../types/nolli").ReviewItem} ReviewItem
 * @typedef {import("../types/nolli").TableRow} TableRow
 * @typedef {import("../types/nolli").NodeDiff} NodeDiff
 * @typedef {import("../types/nolli").EdgeDiff} EdgeDiff
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

/** @type {ReviewItem[]} in the panel's order */
let items = [];
/** @type {Map<string, TableRow>} the changes preset's changed boxes and edges, by id */
let rows = new Map();
/** @type {ReviewVerdict[]} worst first */
let verdicts = [];
/** @type {ReviewHandlers | null} */
let handlers = null;
/** @type {Set<string>} rows shown expanded */
const open = new Set();

/** The changes listed: those in the roles shown (Files in the sidebar). */
const shownItems = () => items.filter(it => (state.roles || ["source"]).includes(it.role));
/** What j / k step through: the verdicts on something in the diagram, then the changes listed. */
export function visibleItems() { return [...verdicts.filter(v => v.ends.length), ...shownItems()]; }
/** A change or a verdict. @param {string} id @returns {ReviewItem | ReviewVerdict | null} */
export const itemById = id => items.find(it => it.id === id) || verdicts.find(v => v.id === id) || null;

/** The rows of a table of the model (GET /api/scene?f=table); none if it cannot have one (a snapshot has
 * no changes). @param {string} key the model's key @param {string} rest the table's and projection's parameters */
async function fetchTable(key, rest) {
  const res = await fetch(`/api/scene?f=table&k=${encodeURIComponent(key)}&${rest}`);
  return res.ok ? /** @type {TableRow[]} */ ((await res.json()).rows) : [];
}

/** The review's verdicts on a model, worst first: a table of the change itself, each node with the verdicts
 * that start there. @param {string} key the model's key */
export async function fetchVerdicts(key) {
  const vs = (await fetchTable(key, "required_overlay=verdicts&overlays=verdicts&p=none")).flatMap(r => r.verdicts?.starting || []);
  return vs.sort((a, b) => RANK[b.verdict] - RANK[a.verdict] || +a.id.slice(1) - +b.id.slice(1));
}

/** Fetch the model's changes (in every role: the panel shows those in the roles shown) and verdicts; empty for
 * a snapshot. @param {string} key the model's key */
export async function loadItems(key) {
  const changed = "required_overlay=change&overlays=change&p=changes&r=" + ALL_ROLES.join(",");
  const [got, vs] = await Promise.all([fetchTable(key, changed), fetchVerdicts(key)]);
  rows = new Map(got.map(r => [r.id, r]));
  items = listChanges(got);
  verdicts = vs;
  open.clear();
  if (key) follow(key, () => handlers?.reload());  // not awaited: the panel draws the block when it comes
  return items;
}

/** The boxes around a row's box, outermost first, as far as the table has them. @param {string | null | undefined} id */
function around(id) {
  const out = [];
  for (let p = id ? rows.get(id)?.parent : null; p; p = rows.get(p)?.parent) out.unshift(p);
  return out;
}

const WHAT = /** @type {Record<string, string>} */ ({ file: "module", directory: "package", package: "external package" });
const CATEGORIES = ["component", "dependency", "content"];

/** What the panel lists of the changed boxes and edges: the top of each added or removed subtree, each
 * edited file and each changed edge, source first, then by category, role, area and title.
 * @param {TableRow[]} got @returns {ReviewItem[]} */
function listChanges(got) {
  /** @type {ReviewItem[]} */
  const out = [];
  for (const r of got) {
    const c = r.change;
    if (!c) continue;
    const up = r.src ? [...new Set([...around(r.src), ...around(r.dst)])] : around(r.id);
    const top = r.src ? around(r.src)[0] ?? r.src : up[0] ?? r.id, base = { id: r.id, status: c.status, role: c.role || "source", area: top, reveal: up, change: c };
    if (r.src && r.dst) {
      const e = /** @type {EdgeDiff} */ (c);
      out.push({ ...base, category: "dependency", title: `${r.src} → ${r.dst}  ${e.base}→${e.head}` });
      continue;
    }
    const n = /** @type {NodeDiff} */ (c), parent = r.parent ? rows.get(r.parent)?.change : null;
    if ((n.status === "added" || n.status === "removed") && parent?.status !== n.status && r.kind !== "group" && r.kind !== "unresolved") {
      const renamed = n.old_path ? ` (renamed from ${n.old_path})` : n.renamed_to ? ` (renamed to ${n.renamed_to})` : "";
      out.push({ ...base, category: "component", title: `${WHAT[r.kind] || r.kind} ${r.id}${renamed}` });
    } else if (r.kind === "file" && n.git && n.status === "changed")
      out.push({ ...base, category: "content", title: `${r.id} (+${n.added} −${n.removed})` });
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
/** @param {{ verdict: string, reason: string } | null | undefined} v */
const verdictSpan = v => v ? `<span class="vd ${esc(v.verdict)}" title="${esc(v.verdict + ": " + v.reason)}">${markHTML(v.verdict)}</span>` : "";

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
  if (it.category === "dependency") { const e = /** @type {EdgeDiff} */ (it.change); html += factList(e.changes || [], e.more || 0); }
  const n = /** @type {NodeDiff} */ (it.change);
  const classes = it.category === "dependency" ? [] : (n.classes || []).filter(c => it.category === "component" ? c.status === it.status : c.added || c.removed);
  if (classes.length) html += `<div class="dt-sub">classes</div><ul class="facts">${classes.map(c => it.category === "component" ? `<li>${esc(c.name)}</li>`
    : `<li>${esc(c.name)} <span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></li>`).join("")}</ul>`;
  if (it.category === "content" && n.hunks?.length) html += `<div class="hunks">${n.hunks.map(h => link(h.link, `L${h.new[0]}${h.new[1] > 1 ? "–" + (h.new[0] + h.new[1] - 1) : ""}`)).join(" ")}${n.more_hunks ? ` … ${n.more_hunks} more` : ""}</div>`;
  const where = "link" in n && n.link ? n.link : it.category === "dependency" ? /** @type {EdgeDiff} */ (it.change).changes?.[0]?.link : null;
  html += `<div class="muted">${where ? link(where, "↗ diff") + " · " : ""}${esc(it.role)} · in ${esc(it.area)}</div>`;
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
  </div><div class="rv-list">`;
  // the review: its heading has the verdicts' counts, where they came from and "again" (reviewer.js)
  const tally = ORDER.filter(v => counts[v]).map(v => `<span class="vd ${v}" title="${v}">${markHTML(v)}</span>${counts[v]}`).join(" ");
  html += `<div class="rv-group rr-group"><span>Review</span>${tally ? `<span class="rr-tally">${tally}</span>` : ""}<span id="rr-head" class="rr-head"></span></div>
    <div id="rv-review" class="rr"></div>`;
  for (const v of verdicts) {
    const cur = state.item === v.id ? " current" : "", off = v.ends.length ? "" : " off";
    html += `<div class="rv-row rv-verdict v-${esc(v.verdict)}${cur}${off}" data-item="${esc(v.id)}"><div class="rv-item">${verdictSpan(v)}<span class="t"><b>${esc(v.label)}</b>${v.ends.length ? "" : ` <span class="muted">(not in the diagram)</span>`}<br>${inlineHTML(v.reason)}</span></div></div>`;
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
