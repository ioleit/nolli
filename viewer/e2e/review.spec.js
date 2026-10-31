// The review view on a made-up change (tests/fixtures/review_repo.py): where the agent's
// verdicts are drawn, what their hover cards say, and how they look. One screenshot per
// thing to look at, cropped to it; `npm run e2e:update` remakes them after a deliberate change.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";

// The page loads ELK from a CDN; the tests serve the same version from node_modules, so they
// run offline and the layout cannot change under them.
const ELK = readFileSync(createRequire(import.meta.url).resolve("elkjs/lib/elk.bundled.js"));

/** Open the fixture's review, settled. @param {import("@playwright/test").Page} page @param {string} [hash] */
async function open(page, hash = "") {
  await page.route("https://cdn.jsdelivr.net/npm/elkjs@*/lib/elk.bundled.js",
    r => r.fulfill({ body: ELK, contentType: "application/javascript" }));
  await page.goto("/#d=ledger-review" + hash);
  await expect(page.locator("#root g.verdict").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/** The part of the diagram around some elements, with a margin. @param {import("@playwright/test").Page} page @param {import("@playwright/test").Locator[]} els */
async function around(page, els, pad = 16) {
  const stage = await page.locator("#stage").boundingBox();
  const boxes = (await Promise.all(els.map(e => e.boundingBox()))).filter(b => b !== null);
  if (!stage || !boxes.length) throw new Error("nothing to crop to");
  const x = Math.max(stage.x, Math.min(...boxes.map(b => b.x)) - pad), y = Math.max(stage.y, Math.min(...boxes.map(b => b.y)) - pad);
  const w = Math.min(stage.x + stage.width, Math.max(...boxes.map(b => b.x + b.width)) + pad) - x;
  const h = Math.min(stage.y + stage.height, Math.max(...boxes.map(b => b.y + b.height)) + pad) - y;
  return { x, y, width: w, height: h };
}

const tip = (/** @type {import("@playwright/test").Page} */ page) => page.locator("#tip");
const node = (/** @type {import("@playwright/test").Page} */ page, /** @type {string} */ id) => page.locator(`#root g.node[data-id="${id}"]`);
// a box's mark is drawn above every box (draw.js), carrying the box's id
const boxMark = (/** @type {import("@playwright/test").Page} */ page, /** @type {string} */ id) => page.locator(`#root g.verdict[data-id="${id}"]`);

test("problems and questions are marked in the diagram, fine verdicts only in the panel", async ({ page }) => {
  await open(page);
  const marks = page.locator("#root g.verdict");
  await expect(marks).toHaveCount(4);
  await expect(page.locator("#root g.edge g.verdict.problem")).toHaveCount(1);
  await expect(page.locator("#root g.edge g.verdict.question")).toHaveCount(2);
  await expect(page.locator("#root g.edge.http g.verdict.question")).toHaveCount(1);
  await expect(boxMark(page, "fs:ledger/storage/cache.py")).toHaveClass("verdict question");
  await expect(page.locator("#root g.verdict.fine")).toHaveCount(0);
  // the glyph stays white on a changed or added edge, whose labels take the change's colour
  for (const t of await marks.locator("text").all()) await expect(t).toHaveCSS("fill", "rgb(255, 255, 255)");

  const rows = page.locator("#review .rv-verdict");
  await expect(rows).toHaveCount(7);
  // worst first, then in file order
  await expect(rows.locator(".vd")).toHaveClass(["vd problem", "vd question", "vd question", "vd question", "vd fine", "vd fine", "vd fine"]);
  await expect(page.locator("#review .rv-verdicts")).toContainText("1 ? 3 ✓ 3");
});

test("a review opens on its worst verdict, with the card that explains it", async ({ page }) => {
  await open(page);
  await expect(page).toHaveURL(/item=V1/);
  await expect(tip(page)).toBeVisible();
  await expect(tip(page)).toContainText("Bypasses the stores");
  // #status ends in timings, different on every run
  await expect(page.locator("#stage")).toHaveScreenshot("problem-on-edge.png", { mask: [page.locator("#status")] });
});

test("hovering a question on a box shows its reason", async ({ page }) => {
  await open(page);
  await page.keyboard.press("Escape");
  await expect(tip(page)).toBeHidden();
  const box = node(page, "fs:ledger/storage/cache.py");
  await boxMark(page, "fs:ledger/storage/cache.py").locator("circle").hover();
  await expect(tip(page)).toContainText("Never invalidated");
  await expect(tip(page)).toContainText("Is that intended?");
  await expect(page).toHaveScreenshot("question-on-box.png", { clip: await around(page, [box, tip(page)]) });
});

test("hovering a question on an HTTP edge shows its reason", async ({ page }) => {
  await open(page);
  // the view opens on the problem, with the HTTP API above it out of sight: go there first
  await page.locator("#review .rv-verdict", { hasText: "Unpaged" }).click();
  await expect(page).toHaveURL(/item=V4/);
  await page.keyboard.press("Escape");
  await expect(tip(page)).toBeHidden();
  const mark = page.locator("#root g.edge.http g.verdict");
  await mark.locator("circle").hover();
  await expect(tip(page)).toContainText("Unpaged");
  await expect(page).toHaveScreenshot("question-on-http-edge.png", { clip: await around(page, [mark, tip(page)], 40) });
});

test("the review panel lists every verdict, worst first", async ({ page }) => {
  await open(page);
  await expect(page.locator("#review")).toHaveScreenshot("verdict-list.png");
});

test("a fine verdict goes to its box from the panel, without a mark on it", async ({ page }) => {
  await open(page);
  await page.locator("#review .rv-verdict", { hasText: "Good riddance" }).click();
  await expect(page).toHaveURL(/item=V5/);
  const box = node(page, "fs:ledger/services/legacy_export.py");
  await expect(box).toHaveClass(/\blit\b/);
  await expect(boxMark(page, "fs:ledger/services/legacy_export.py")).toHaveCount(0);
  await expect(tip(page)).toContainText("Good riddance");
  await expect(page).toHaveScreenshot("fine-on-removed-box.png", { clip: await around(page, [box, tip(page)]) });
});
