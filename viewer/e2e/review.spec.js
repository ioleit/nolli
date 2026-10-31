// The review view on a made-up change (tests/fixtures/review_repo.py): where the agent's
// verdicts are drawn, what their hover cards say, and how they look. One screenshot per
// thing to look at, cropped to it; `npm run e2e:update` remakes them after a deliberate change.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, test } from "./guard.js";

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

/** Go to a verdict from the panel, put its card away, and hover its mark instead.
 * @param {import("@playwright/test").Page} page @param {string} row text in the verdict's row @param {import("@playwright/test").Locator} mark */
async function hoverMark(page, row, mark) {
  await page.locator("#review .rv-verdict", { hasText: row }).click();
  // the jump may unfold boxes first; the card it pins comes last
  await expect(tip(page)).toContainText(row);
  await page.waitForLoadState("networkidle");
  await page.keyboard.press("Escape");
  await expect(tip(page)).toBeHidden();
  await mark.locator("circle").hover();
  await expect(tip(page)).toContainText(row);
}

test("every verdict but fine is marked in the diagram, with its chess annotation", async ({ page }) => {
  await open(page);
  const marks = page.locator("#root g.verdict");
  await expect(marks).toHaveCount(7);
  await expect(page.locator("#root g.edge g.verdict.blunder text")).toHaveText("??");
  await expect(page.locator("#root g.edge g.verdict.problem text")).toHaveText("?");
  await expect(page.locator("#root g.edge g.verdict.question text")).toHaveText(["?!", "?!"]);
  await expect(page.locator("#root g.edge.http g.verdict.question")).toHaveCount(1);
  await expect(boxMark(page, "fs:ledger/storage/cache.py").locator("text")).toHaveText("?!");
  await expect(boxMark(page, "fs:ledger/services/legacy_export.py").locator("text")).toHaveText("!!");
  // the good one is on views/alerts, inside web/src/, which is folded at first and shows it
  await expect(boxMark(page, "fs:web/src").locator("text")).toHaveText("!");
  await expect(page.locator("#root g.verdict.fine")).toHaveCount(0);
  // the glyph stays white on a changed or added edge, whose labels take the change's colour
  for (const t of await marks.locator("text").all()) await expect(t).toHaveCSS("fill", "rgb(255, 255, 255)");

  const rows = page.locator("#review .rv-verdict");
  // most attention first: doubts, then praise, then fine; in file order within a kind
  await expect(rows.locator(".vd")).toHaveClass(["blunder", "problem", "question", "question", "question", "brilliant", "good", "fine"].map(v => "vd " + v));
  await expect(page.locator("#review .rv-verdicts")).toContainText("?? 1 ? 1 ?! 3 !! 1 ! 1 ✓ 1");
});

test("a review opens on its worst verdict, a blunder, with the card that explains it", async ({ page }) => {
  await open(page);
  await expect(page).toHaveURL(/item=V1/);
  await expect(tip(page)).toBeVisible();
  await expect(tip(page)).toContainText("New cycle");
  // #status ends in timings, different on every run
  await page.addStyleTag({ content: "#status { visibility: hidden }" });
  await expect(page.locator("#stage")).toHaveScreenshot("blunder-on-edge.png");
});

test("hovering a problem on an edge shows its reason", async ({ page }) => {
  await open(page);
  const mark = page.locator("#root g.edge g.verdict.problem");
  await hoverMark(page, "Skips the store layer", mark);
  await expect(page).toHaveScreenshot("problem-on-edge.png", { clip: await around(page, [mark, tip(page)], 40) });
});

test("hovering a question on a box shows its reason", async ({ page }) => {
  await open(page);
  const mark = boxMark(page, "fs:ledger/storage/cache.py");
  await hoverMark(page, "A layer inside storage/", mark);
  await expect(tip(page)).toContainText("between services/ and storage/");
  await expect(page).toHaveScreenshot("question-on-box.png", { clip: await around(page, [node(page, "fs:ledger/storage/cache.py"), tip(page)]) });
});

test("hovering a question on an HTTP edge shows its reason", async ({ page }) => {
  await open(page);
  const mark = page.locator("#root g.edge.http g.verdict");
  await hoverMark(page, "Client becomes a hub", mark);
  await expect(page).toHaveScreenshot("question-on-http-edge.png", { clip: await around(page, [mark, tip(page)], 40) });
});

test("hovering a brilliant on a removed box shows its reason", async ({ page }) => {
  await open(page);
  const id = "fs:ledger/services/legacy_export.py";
  await hoverMark(page, "Orphan removed", boxMark(page, id));
  await expect(page).toHaveScreenshot("brilliant-on-removed-box.png", { clip: await around(page, [node(page, id), tip(page)]) });
});

test("hovering a good on a box shows its reason", async ({ page }) => {
  await open(page);
  const id = "fs:web/src/views/alerts.ts";
  await hoverMark(page, "Follows the pattern", boxMark(page, id));
  await expect(boxMark(page, id).locator("text")).toHaveText("!");
  await expect(page).toHaveScreenshot("good-on-box.png", { clip: await around(page, [node(page, id), tip(page)]) });
});

test("the review panel lists every verdict, most attention first", async ({ page }) => {
  await open(page);
  await expect(page.locator("#review")).toHaveScreenshot("verdict-list.png");
});

test("a fine verdict goes to its box from the panel, without a mark on it", async ({ page }) => {
  await open(page);
  await page.locator("#review .rv-verdict", { hasText: "the one box that assembles" }).click();
  await expect(page).toHaveURL(/item=V8/);
  const id = "fs:ledger/api/routes.py";
  await expect(node(page, id)).toHaveClass(/\blit\b/);
  await expect(boxMark(page, id)).toHaveCount(0);
  await expect(tip(page)).toContainText("the one box that assembles");
  await expect(page).toHaveScreenshot("fine-on-box.png", { clip: await around(page, [node(page, id), tip(page)]) });
});
