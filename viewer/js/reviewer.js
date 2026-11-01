// The review, at the top of the review panel's list: where the verdicts shown came from, and
// "Review structural changes", which starts a review agent on the server (POST /api/review,
// viewer/reviews.py). A run takes minutes: the block follows it (GET /api/review) wherever
// the reader goes meanwhile, says what the agent is doing, and can cancel it; the tab's
// title says it too. Without an API key where nolli runs the button is disabled, and "Other
// ways to review" says how to add one, and how to review with your own coding agent
// (nolli's skill), or by hand (review.md, then load the verdicts) where the server stores them.

import { inlineHTML } from "./md.js";

/**
 * @typedef {import("../types/nolli").ReviewStatus} ReviewStatus
 * @typedef {import("../types/nolli").SceneDiff} SceneDiff
 */

/** @param {unknown} s */
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** A magnifier over two boxes and the edge between them: looking at the structure. */
export const ICON = `<svg class="ricon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="6.5" cy="6.5" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/>
  <path d="M10.2 10.2 14.5 14.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
  <rect x="3.4" y="3.6" width="2.6" height="2.2" rx=".5" fill="currentColor"/><rect x="7" y="7.2" width="2.6" height="2.2" rx=".5" fill="currentColor"/>
  <path d="M4.7 5.8v2.5H7" fill="none" stroke="currentColor" stroke-width="1"/></svg>`;

/** @type {ReviewStatus | null} */
let status = null;
let name = "";
/** @type {SceneDiff | null} */
let diff = null;
let ways = false;  // "Other ways to review" open
/** @type {ReturnType<typeof setTimeout> | null} */
let timer = null;
let busy = false;  // a start or cancel request on its way
const baseTitle = document.title;
/** @type {() => void} */
let onVerdicts = () => {};

const running = () => !!status?.run && (status.run.state === "running" || status.run.state === "queued");

/**
 * Follow the review of a dataset: fetch its status now and while a run is under way.
 * @param {string} dataset @param {() => void} verdictsChanged called when a run has stored new verdicts
 */
export async function follow(dataset, verdictsChanged) {
  if (dataset !== name) { status = null; ways = false; }
  name = dataset; onVerdicts = verdictsChanged;
  await poll();
}

async function poll() {
  if (timer) { clearTimeout(timer); timer = null; }
  const was = status?.run?.state;
  try {
    const res = await fetch("/api/review?d=" + encodeURIComponent(name));
    if (!res.ok) return;
    status = await res.json();
  } catch { /* the server restarting: try again below */ }
  if (was && was !== "done" && status?.run?.state === "done") onVerdicts();
  draw();
  if (running()) timer = setTimeout(poll, 1500);
}

/** @param {"start" | "cancel"} what */
async function act(what) {
  if (busy) return;
  busy = true; draw();
  try {
    const res = await fetch(`/api/review${what === "cancel" ? "/cancel" : ""}?d=` + encodeURIComponent(name), { method: "POST" });
    const body = await res.json();
    if (body.verdicts !== undefined) status = body;
  } finally { busy = false; }
  draw();
  if (running()) timer = setTimeout(poll, 1000);
}

/** m:ss @param {number} s */
const clock = s => { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

/** "4 min ago", "yesterday", or the date. @param {string | undefined} iso */
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!isFinite(s)) return "";
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Where the verdicts shown came from, in a few words; the details in its title. */
function origin() {
  const v = status?.verdicts;
  if (!v) return "";
  const when = ago(v.at);
  if (v.by === "ci") {
    const dropped = v.dropped ? `; ${v.dropped} did not fit this drawing` : "";
    return `<a href="${esc(v.link)}" target="_blank" rel="noopener" title="nolli CI's review of this commit, from its PR comment${esc(dropped)}">nolli CI · ${esc(when)} ↗</a>`;
  }
  if (v.by === "agent") return `<span title="${esc(`A review agent (${v.model}) run here, in ${clock(v.seconds || 0)}`)}">agent · ${esc(when)}</span>`;
  if (v.by === "upload") return `<span title="Loaded from a file">file · ${esc(when)}</span>`;
  return "";
}

/** What nolli's CI says about this commit, when there are no verdicts yet. */
function ciNote() {
  const c = status?.ci;
  if (!c?.applies) return "";
  if (c.error) return `Could not ask GitHub for a nolli CI review: ${esc(c.error)}`;
  if (c.found === "no-review") return `nolli CI ran on this commit without a review agent (no API key in the repository) · <a href="${esc(c.link)}" target="_blank" rel="noopener">comment ↗</a>`;
  if (c.found === "earlier") return `nolli CI reviewed an earlier push (${esc(c.head)}), not this commit · <a href="${esc(c.link)}" target="_blank" rel="noopener">comment ↗</a>`;
  if (c.found === "verdicts" && c.dropped) return "nolli CI's verdicts for this commit do not fit this drawing (another nolli version?)";
  return "No nolli CI review of this commit.";
}

/** The prompt that asks a coding agent with nolli's skill for this review. */
function agentPrompt() {
  if (!diff) return "Review this change with nolli";
  if (diff.pr && diff.remote) return `Review ${diff.remote}/pull/${diff.pr} with nolli`;
  return `Review ${diff.base_short}..${diff.head_short} with nolli`;
}

/** @param {string} text */
const code = text => `<div class="rr-code"><code>${esc(text)}</code><button class="rr-copy" data-copy="${esc(text)}" title="copy">copy</button></div>`;

/** "Other ways to review": an API key, your own coding agent, by hand. */
function waysHTML() {
  const st = status;
  const why = st?.why_not;
  const key = why === "package"
    ? `<p>This nolli has no API client: install it with the extra, then start it again with the key set.</p>${code("pip install 'nolli-review[api]'")}`
    : "";
  const keyStep = why === "key" || why === "package"
    ? `<li><b>Add an API key</b> where nolli runs and start it again.
        ${key}${code(st?.server === "view" ? "ANTHROPIC_API_KEY=sk-ant-… nolli view …" : "ANTHROPIC_API_KEY=sk-ant-… nolli serve")}</li>`
    : why === "repo"
      ? `<li><b>Here, with the repository:</b> this viewer was opened on a dataset, without the repository the agent reads the code from. Open the change itself instead, e.g. <code>nolli view ${esc(diff?.pr ? "'#" + diff.pr + "'" : (diff?.base_short || "") + ".." + (diff?.head_short || ""))}</code>.</li>`
      : "";
  const own = `<li><b>With your own coding agent</b> (Claude Code, or another that reads skills): install nolli's review skill once,
      ${code("pipx install nolli-review && nolli agent skill --install ~/.claude/skills")}
      then ask, in a clone of the repository:${code(agentPrompt())}
      The agent builds this same picture on your machine and opens its verdicts in a viewer there.</li>`;
  const hand = st?.uploads
    ? `<li><b>By hand:</b> give any agent <a href="/data/${esc(name)}/review.md" target="_blank">review.md</a> (the diagram and how to answer),
        save its answer as a .json file and <label class="file">load it here…<input type="file" id="rv-file" accept=".json,application/json"></label></li>`
    : "";
  return `<ol class="rr-ways">${keyStep}${own}${hand}</ol>`;
}

/** Why the button is disabled, in a few words: a word the server uses, or the server's own sentence. @param {string | null | undefined} why */
const whyNot = why => ({ key: "Needs an Anthropic API key where nolli runs.", package: "Needs the API client (nolli-review[api]) where nolli runs.",
  repo: "Needs the change's repository, which this viewer was not given.", dataset: "This dataset has no review diagram (built by an older nolli?)." })[why || ""] || why || "";

/** Draw the review: its part of the Review heading (#rr-head: where the verdicts came from,
 * "Review again", other ways) and what goes under it (#rv-review: a run under way, how a run
 * ended, and, without verdicts, the button and how else to get a review). */
export function draw() {
  const head = document.getElementById("rr-head"), box = document.getElementById("rv-review");
  if (!head || !box) return;
  const st = status;
  const run = st?.run;
  const has = !!st?.verdicts;
  const can = !!st?.can_run && !busy;
  const waysBtn = `<button class="rr-link" id="rr-ways" aria-expanded="${ways}" title="Other ways to review">other ways</button>`;
  head.innerHTML = has ? `${origin()}<span class="rr-tools">${st?.can_run && !running()
    ? `<button id="rr-start" class="rr-again"${can ? "" : " disabled"} title="${esc(`Run a review agent (${st?.model}) again; these verdicts stay until its own are ready`)}">${ICON}again</button>` : ""}${waysBtn}</span>` : "";
  let html = "";
  if (run && running()) {
    const waiting = run.state === "queued";
    html += `<div class="rr-run" role="status" title="${has ? "The verdicts shown stay until the new ones are ready" : "You can keep looking around meanwhile"}">
      <span class="spin" aria-hidden="true"></span><b>${waiting ? "Waiting for another review" : "Reviewing"}…</b>
      <span class="rr-step">${waiting ? "" : inlineHTML(run.step || "starting")}</span>
      <span class="rr-clock">${clock(waiting ? run.waiting : run.seconds)}</span>
      <button class="rr-cancel" id="rr-cancel"${busy ? " disabled" : ""}>Cancel</button></div>`;
  } else if (run?.state === "failed") html += `<div class="rr-fail">The review failed: ${esc(run.error)}</div>`;
  else if (run?.state === "cancelled") html += `<div class="rr-note">Review cancelled.</div>`;
  if (!has && !running() && st) {
    const label = run?.state === "failed" ? "Try again" : "Review structural changes";
    html += `<div class="rr-act">
      <button id="rr-start" class="rr-start"${can ? "" : " disabled"} title="${esc(can ? `A review agent (${st.model}) judges every changed box and edge` : whyNot(st.why_not))}">${ICON}<span>${label}</span></button>
      ${st.can_run ? "" : `<span class="rr-why">${esc(whyNot(st.why_not))}</span>`}
    </div>`;
    if (st.can_run && !run) html += `<div class="rr-meta">A review agent judges every changed box and edge of this drawing. It takes a few minutes; you can keep looking around meanwhile.</div>`;
    if (st.ci) html += `<div class="rr-meta">${ciNote()}</div>`;
    html += `<details class="rr-more"${ways || !st.can_run ? " open" : ""}><summary>${st.can_run ? "Other ways to review" : "How to get a review"}</summary>${waysHTML()}</details>`;
  } else if (has && ways) html += waysHTML();
  box.innerHTML = html;
  document.title = running() ? `◌ Reviewing · ${baseTitle}` : baseTitle;
  for (const el of [head, box]) {
    el.querySelector("#rr-start")?.addEventListener("click", () => act("start"));
    el.querySelector("#rr-ways")?.addEventListener("click", () => { ways = !ways; draw(); });
  }
  box.querySelector("#rr-cancel")?.addEventListener("click", () => act("cancel"));
  box.querySelector("details")?.addEventListener("toggle", ev => { ways = /** @type {HTMLDetailsElement} */ (ev.target).open; });
  box.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(/** @type {HTMLElement} */ (b).dataset.copy || ""); b.textContent = "copied"; } catch { b.textContent = "select it"; }
    setTimeout(() => { b.textContent = "copy"; }, 1500);
  }));
  const file = /** @type {HTMLInputElement | null} */ (box.querySelector("#rv-file"));
  file?.addEventListener("change", () => upload(file));
}

/** The change the panel shows, for the prompts. @param {SceneDiff} d */
export function setDiff(d) { diff = d; }

/** @param {HTMLInputElement} input */
async function upload(input) {
  const f = input.files && input.files[0];
  if (!f || !name) return;
  const res = await fetch("/api/verdicts?d=" + encodeURIComponent(name), { method: "POST", body: await f.text() });
  const body = await res.json();
  if (!res.ok) { alert("verdicts not loaded: " + body.error); return; }
  ways = false;
  onVerdicts();
  await poll();
}

