// Build one self-contained HTML page of the failed screenshot comparisons, so they can be
// looked at in a browser without unpacking anything: for each test that failed
// toHaveScreenshot, the expected image, the actual one, Playwright's diff, and one that
// blinks between expected and actual while the pointer is on it. The images are inlined,
// so the page is a single file (CI uploads it unzipped, .github/workflows/ci.yml).
//
// Usage: node e2e/diff_page.mjs [results dir] [output file]
//   results dir  Playwright's output directory (default: test-results)
//   output file  the page to write (default: <results dir>/screenshot-diffs.html)
// It writes nothing, and says so, when no screenshot comparison failed. Run it after
// `npx playwright test`, from viewer/.

import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

const args = process.argv.slice(2);
if (args.includes("-h") || args.includes("--help")) {
  console.log(readFileSync(new URL(import.meta.url), "utf8").split("\n").filter((l) => l.startsWith("//")).map((l) => l.slice(3)).join("\n"));
  process.exit(0);
}
const results = args[0] ?? "test-results";
const out = args[1] ?? join(results, "screenshot-diffs.html");
if (!existsSync(results)) {
  console.error(`diff_page: no results directory ${results}; run npx playwright test first (from viewer/)`);
  process.exit(1);
}

/** Find every *-diff.png under dir, with its expected and actual image beside it. */
function findComparisons(dir) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !entry.name.endsWith("-diff.png")) continue;
    const folder = entry.parentPath ?? entry.path;
    const stem = entry.name.slice(0, -"-diff.png".length);
    const image = (kind) => join(folder, `${stem}-${kind}.png`);
    if (!existsSync(image("expected")) || !existsSync(image("actual"))) continue;
    found.push({ test: relative(dir, folder), screenshot: `${stem}.png`, expected: image("expected"), actual: image("actual"), diff: image("diff") });
  }
  return found.sort((a, b) => a.test.localeCompare(b.test));
}

/** Return a file as a data: URL. */
const inline = (path) => `data:image/png;base64,${readFileSync(path).toString("base64")}`;

/** Escape text for HTML. */
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const comparisons = findComparisons(results);
if (comparisons.length === 0) {
  console.log(`diff_page: no failed screenshot comparisons in ${results}; nothing written`);
  process.exit(0);
}

const sections = comparisons.map((c) => {
  const [expected, actual, diff] = [inline(c.expected), inline(c.actual), inline(c.diff)];
  return `<section>
  <h2>${esc(c.screenshot)}</h2>
  <p class="test">${esc(c.test)}</p>
  <div class="row">
    <figure><figcaption>expected</figcaption><img src="${expected}"></figure>
    <figure><figcaption>actual</figcaption><img src="${actual}"></figure>
    <figure><figcaption>diff (red: differs)</figcaption><img src="${diff}"></figure>
    <figure><figcaption>blink: point at it</figcaption><img class="blink" src="${expected}" data-a="${expected}" data-b="${actual}"></figure>
  </div>
</section>`;
});

const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screenshot diffs</title>
<style>
  body { font: 14px/1.4 system-ui, sans-serif; margin: 16px; background: #fff; color: #222; }
  h1 { font-size: 18px; } h2 { font-size: 15px; margin: 0; }
  section { border-top: 1px solid #ddd; padding: 12px 0; }
  .test { color: #666; margin: 2px 0 8px; font-family: ui-monospace, monospace; font-size: 12px; }
  .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
  figure { margin: 0; } figcaption { font-size: 12px; color: #666; }
  img { display: block; max-width: 100%; border: 1px solid #ccc; image-rendering: pixelated; }
  .blink { cursor: crosshair; outline: 2px solid transparent; } .blink.showing-b { outline-color: #d33; }
</style></head><body>
<h1>${comparisons.length} failed screenshot comparison${comparisons.length === 1 ? "" : "s"}</h1>
${sections.join("\n")}
<script>
  for (const img of document.querySelectorAll(".blink")) {
    let timer;
    img.addEventListener("mouseenter", () => { timer = setInterval(() => { const b = !img.classList.contains("showing-b"); img.src = b ? img.dataset.b : img.dataset.a; img.classList.toggle("showing-b", b); }, 500); });
    img.addEventListener("mouseleave", () => { clearInterval(timer); img.src = img.dataset.a; img.classList.remove("showing-b"); });
  }
</script>
</body></html>
`;

writeFileSync(out, page);
console.log(`diff_page: ${comparisons.length} comparison(s) -> ${out}`);
