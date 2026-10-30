// Layout stability over single unfold/fold steps from each dataset's default cut (bench.html).
// The result is also left as JSON in #result and window.benchResult for scripts.

import { ENGINES, isAnchor, layout } from "./layout/index.js";
import { compare } from "./stability.js";

/**
 * @typedef {import("../types/nolli").Scene} Scene
 * @typedef {import("./stability.js").Stability} Stability
 * @typedef {{ id: string, action: "fold" | "unfold", expanded: string[] }} Step
 * @typedef {{ dataset: string, engine: string, steps: number, order: number, orderMin: number, knn: number, residual: number, msPerLayout: number, control?: string }} Row
 */
import { byId } from "./dom.js";

const params = new URLSearchParams(location.search);
const $ = byId;
/** @param {number} x */
const fmt = (x, d = 3) => x.toFixed(d);

/** @param {string} d @param {string[]} [expanded] @returns {Promise<Scene>} */
async function scene(d, expanded) {
  const p = new URLSearchParams({ d });
  if (expanded) p.set("x", expanded.join(","));
  const res = await fetch("/api/scene?" + p);
  if (!res.ok) throw new Error(`${d}: ${(await res.json()).error}`);
  return res.json();
}

/** The steps from a cut: unfold each folded box, fold each open container. @param {Scene} sc @returns {Step[]} */
function steps(sc) {
  /** @type {Step[]} */
  const out = [];
  for (const n of sc.nodes) {
    if (isAnchor(n) || !n.foldable) continue;
    const x = new Set(sc.state.expanded);
    if (n.container) x.delete(n.id); else x.add(n.id);
    out.push({ id: n.id, action: n.container ? "fold" : "unfold", expanded: [...x] });
  }
  return out;
}

const mean = (/** @type {number[]} */ xs) => xs.reduce((s, x) => s + x, 0) / (xs.length || 1);

(async () => {
  /** @type {string[]} */
  const sets = (await (await fetch("/api/list")).json()).map((/** @type {{ name: string }} */ s) => s.name).sort();
  const ds = params.get("d"), es = params.get("engines");
  const datasets = ds ? ds.split(",") : sets;
  const engines = es ? es.split(",") : Object.keys(ENGINES);
  /** @type {Row[]} */
  const rows = [];
  /** @type {{ dataset: string, engine: string, results: (Omit<Step, "expanded"> & Stability)[] }[]} */
  const detail = [];
  for (const d of datasets) {
    const base = await scene(d);
    const todo = steps(base);
    /** @type {Scene[]} */
    const after = [];
    for (const st of todo) { $("status").textContent = `${d}: fetching ${st.action} ${st.id}`; after.push(await scene(d, st.expanded)); }
    for (const engine of engines) {
      /** @type {import("../types/nolli").LayoutOptions} */
      const opts = { engine, direction: "DOWN", seed: d };
      const t0 = performance.now();
      const laid0 = await layout(base, opts);
      const results = [];
      for (let i = 0; i < todo.length; i++) {
        $("status").textContent = `${d} · ${engine}: ${i + 1}/${todo.length}`;
        const laid1 = await layout(after[i], { ...opts, previous: laid0, toggled: todo[i].id });
        const { expanded: _, ...step } = todo[i];
        results.push({ ...step, ...compare(laid0, laid1, base, after[i]) });
      }
      const ms = Math.round((performance.now() - t0) / (todo.length + 1));
      // control: the same cut laid out again must score order 1, knn 1, residual 0
      const control = compare(laid0, await layout(base, opts), base, base);
      rows.push({ dataset: d, engine, steps: results.length, order: mean(results.map(r => r.order)), orderMin: Math.min(...results.map(r => r.order)),
        knn: mean(results.map(r => r.knn)), residual: mean(results.map(r => r.residual)), msPerLayout: ms,
        control: control.order === 1 && control.knn === 1 && control.residual === 0 ? "ok" : `order ${fmt(control.order)} knn ${fmt(control.knn)} residual ${fmt(control.residual)}` });
      detail.push({ dataset: d, engine, results });
    }
  }
  const total = engines.map(engine => { const rs = rows.filter(r => r.engine === engine); const w = rs.reduce((s, r) => s + r.steps, 0) || 1;
    const wm = (/** @type {"order" | "knn" | "residual" | "msPerLayout"} */ k) => rs.reduce((s, r) => s + r[k] * r.steps, 0) / w;
    return { dataset: "all (step-weighted)", engine, steps: w, order: wm("order"), orderMin: Math.min(...rs.map(r => r.orderMin)), knn: wm("knn"), residual: wm("residual"), msPerLayout: Math.round(wm("msPerLayout")) }; });

  const tr = (/** @type {Row} */ r, cls = "") => `<tr class="${cls}"><td>${r.dataset}</td><td>${r.engine}</td><td>${r.steps}</td><td>${fmt(r.order)}</td><td>${fmt(r.orderMin)}</td><td>${fmt(r.knn)}</td><td>${fmt(r.residual)}</td><td>${r.msPerLayout}</td><td>${r.control || ""}</td></tr>`;
  $("out").innerHTML = `<table><tr><th>dataset</th><th>engine</th><th>steps</th><th>order</th><th>order (worst step)</th><th>knn</th><th>residual</th><th>ms/layout</th><th>control</th></tr>${rows.map(r => tr(r)).join("")}${total.map(r => tr(r, "total")).join("")}</table>`
    + detail.map(x => `<details><summary>${x.dataset} · ${x.engine}: each step</summary><table><tr><th>box</th><th>step</th><th>boxes kept</th><th>order</th><th>knn</th><th>residual</th></tr>${x.results.map(r => `<tr><td>${r.id}</td><td>${r.action}</td><td>${r.n}</td><td>${fmt(r.order)}</td><td>${fmt(r.knn)}</td><td>${fmt(r.residual)}</td></tr>`).join("")}</table></details>`).join("");
  $("status").textContent = "done";
  const result = { rows, total, detail };
  /** @type {any} */ (window).benchResult = result;
  $("result").textContent = JSON.stringify(result);
})().catch(e => { $("status").textContent = "error: " + e.message; });
