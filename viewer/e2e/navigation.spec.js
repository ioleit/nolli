// Unfolding and folding on a made-up change (tests/fixtures/nav_repo.py) with two utilities
// and a changed edge between them, which ELK refused while utilities were pinned to its last
// layer, and a verdict on an edge the viewer bundles. A click toggles the box clicked and nothing else, and every step lays out without an
// error (guard.js).

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
  await open(page, "&x=fs:.,fs:tasks,fs:tasks/core");
  await expect(page.locator('#root g.node.utility[data-id="fs:tasks/core/log.py"]')).toBeVisible();
  await expect(page.locator('#root g.node.utility[data-id="fs:tasks/core/clock.py"]')).toBeVisible();
  await expect(page.locator("#root g.edge.d-added")).toHaveCount(1);
});

test("each click unfolds or folds the box clicked, and only that one", async ({ page }) => {
  await open(page, "&x=fs:.,fs:tasks");
  // folded, core is the utility; unfolded, log and clock are, with the new edge between them
  for (const id of ["fs:tasks/api", "fs:tasks/core", "fs:tasks/api", "fs:tasks/jobs", "fs:tasks/core"]) {
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
  await open(page, "&x=fs:.,fs:tasks,fs:tasks/jobs,fs:tasks/store");
  const mark = page.locator("#root g.edge g.verdict.question");
  await expect(mark).toHaveCount(1);
  await expect(page.locator("#root g.verdict[data-id]")).toHaveCount(0);  // no box carries it
  await page.keyboard.press("Escape");
  await mark.locator("circle").hover();
  await expect(page.locator("#tip")).toContainText("Reaches past its area");
  await expect(page.locator("#root g.node.container")).toHaveCount(3);  // tasks, jobs, store: nothing else unfolded
});

