// Unfolding and folding on a made-up change (tests/fixtures/nav_repo.py) with two utilities
// and a changed edge between them, which ELK refused while utilities were pinned to its last
// layer, and a verdict on an edge the viewer bundles. A click toggles the box clicked and nothing else, and every step lays out without an
// error (guard.js). The Mermaid tab draws the same scene and toggles the same way; presets go into the link.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, test } from "./guard.js";

const ELK = readFileSync(createRequire(import.meta.url).resolve("elkjs/lib/elk.bundled.js"));

/** @param {import("@playwright/test").Page} page @param {string} hash */
async function open(page, hash) {
  await page.route("https://cdn.jsdelivr.net/npm/elkjs@*/lib/elk.bundled.js",
    r => r.fulfill({ body: ELK, contentType: "application/javascript" }));
  await page.goto("/#d=tasks" + hash);
  await expect(page.locator("#root g.node").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/** What is unfolded, from the location hash. @param {import("@playwright/test").Page} page */
const unfolded = async page => new Set((new URLSearchParams((await page.evaluate(() => location.hash)).slice(1)).get("x") || "").split(",").filter(Boolean));

/** The unfolded boxes drawn: containers have children in the scene the page shows. @param {import("@playwright/test").Page} page */
const drawnContainers = page => page.evaluate(() => [...document.querySelectorAll("#root g.node.container")].map(g => g.getAttribute("data-id")).sort());

/** Click a box and wait for the picture to settle. @param {import("@playwright/test").Page} page @param {string} id */
async function click(page, id) {
  const before = await page.evaluate(() => location.hash);
  // an unfolded box folds with its − (drawn above the edges, data-fold), a folded one unfolds with a click
  const fold = page.locator(`#root g[data-fold="${id}"] g.fold`);
  await ((await fold.count()) ? fold : page.locator(`#root g.node[data-id="${id}"] > rect`).first()).click();
  // the hash is written once the new picture is drawn
  await expect.poll(() => page.evaluate(() => location.hash)).not.toBe(before);
  await page.waitForLoadState("networkidle");
}

test("two utilities with an edge between them lay out", async ({ page }) => {
  await open(page, "&x=repo:,repo:tasks,repo:tasks/core");
  await expect(page.locator('#root g.node.utility[data-id="repo:tasks/core/log.py"]')).toBeVisible();
  await expect(page.locator('#root g.node.utility[data-id="repo:tasks/core/clock.py"]')).toBeVisible();
  await expect(page.locator("#root g.edge.d-added")).toHaveCount(1);
});

test("each click unfolds or folds the box clicked, and only that one", async ({ page }) => {
  await open(page, "&x=repo:,repo:tasks");
  // folded, core is the utility; unfolded, log and clock are, with the new edge between them
  for (const id of ["repo:tasks/api", "repo:tasks/core", "repo:tasks/api", "repo:tasks/jobs", "repo:tasks/core"]) {
    const before = await unfolded(page);
    await click(page, id);
    const after = await unfolded(page);
    const want = new Set(before); if (want.has(id)) want.delete(id); else want.add(id);
    expect([...after].sort(), `after clicking ${id}`).toEqual([...want].sort());
    const drawn = await drawnContainers(page);
    expect(drawn.includes(id), `${id} drawn ${want.has(id) ? "unfolded" : "folded"}`).toBe(want.has(id));
  }
});

test("a verdict on an edge that is bundled sits on the bundle, not on a box", async ({ page }) => {
  // the review diagram draws worker → store/users/repo; with store's packages folded, the viewer
  // draws worker's three edges into store as one
  await open(page, "&x=repo:,repo:tasks,repo:tasks/jobs,repo:tasks/store");
  const mark = page.locator("#root g.edge g.verdict.question");
  await expect(mark).toHaveCount(1);
  await expect(page.locator("#root g.verdict[data-id]")).toHaveCount(0);  // no box carries it
  await page.keyboard.press("Escape");
  await mark.locator("circle").hover();
  await expect(page.locator("#tip")).toContainText("Reaches past its area");
  await expect(page.locator("#root g.node.container")).toHaveCount(3);  // tasks, jobs, store: nothing else unfolded
});


const MERMAID = createRequire(import.meta.url).resolve("mermaid/dist/mermaid.esm.min.mjs").replace(/mermaid\.esm\.min\.mjs$/, "");

/** The Mermaid ids drawn in the Mermaid tab: boxes and subgraphs. @param {import("@playwright/test").Page} page */
const mermaidIds = page => page.evaluate(() => [...document.querySelectorAll("#mmdpane g.node, #mmdpane g.cluster")]
  .map(g => (/-flowchart-(n_\w+?)-\d+$/.exec(g.id) || /-(n_\w+?)_g$/.exec(g.id) || [])[1]).filter(Boolean).sort());

test("the Mermaid tab draws the scene, and a click there unfolds as in the drawing", async ({ page }) => {
  await page.route("https://cdn.jsdelivr.net/npm/mermaid@11/dist/**",
    r => r.fulfill({ body: readFileSync(MERMAID + new URL(r.request().url()).pathname.split("/dist/")[1]), contentType: "application/javascript" }));
  await open(page, "&p=folded");
  await page.locator('#views [data-view="mermaid"]').click();
  await expect(page.locator("#mmdpane .mmd-svg svg")).toBeVisible();
  const scene = await page.evaluate(async () => (await fetch("/api/scene?" + location.hash.slice(1).replace(/&v=mermaid/, ""))).json());
  const nid = (/** @type {string} */ id) => "n_" + id.replace(/[^A-Za-z0-9]/g, "_");
  expect(await mermaidIds(page)).toEqual(scene.nodes.map((/** @type {{ id: string }} */ n) => nid(n.id)).sort());
  await expect(page.locator("#custom")).toHaveJSProperty("hidden", true);
  // a folded box unfolds, and the cut is no preset any more
  await page.locator(`#mmdpane g.node[id*="-flowchart-${nid("repo:tasks")}-"]`).click();
  await expect.poll(() => unfolded(page)).toContain("repo:tasks");
  await expect(page.locator(`#mmdpane g.cluster[id$="-${nid("repo:tasks")}_g"]`)).toBeVisible();
  await expect(page.locator("#custom")).toHaveJSProperty("hidden", false);
  // a preset goes into the link by name, and both tabs show its cut
  await page.locator("#navtoggle").click();
  await page.locator('#presets [data-preset="folded"]').click();
  await expect.poll(() => page.evaluate(() => location.hash)).toContain("p=folded");
  await expect(page.locator(`#mmdpane g.cluster[id$="-${nid("repo:tasks")}_g"]`)).toHaveCount(0);
  await page.locator('#views [data-view="interactive"]').click();
  await expect(page.locator('#root g.node[data-id="repo:tasks"]:not(.container)')).toBeVisible();
});
