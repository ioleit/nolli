// Copies the viewer's third-party libraries into vendor/ (committed), so the viewer
// works without the internet and from an installed nolli package. Versions come from
// package.json's devDependencies; run `npm run vendor` after changing one.
//
//   vendor/elk.bundled.js    ELK (layered layout), a classic script that sets globalThis.ELK
//   vendor/cytoscape.mjs     Cytoscape as an ES module
//   vendor/fcose.mjs         cytoscape-fcose with its dependencies (cose-base, layout-base) bundled in
//   vendor/mermaid.min.js    Mermaid, a classic script that sets globalThis.mermaid

import { copyFileSync, existsSync, globSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "vendor");
/** A package's directory in node_modules (hoisted, or nested under the package that needs it). */
const pkg = (/** @type {string} */ name) => {
  const top = join(here, "node_modules", name);
  if (existsSync(top)) return top;
  const nested = globSync(`node_modules/*/node_modules/${name}`, { cwd: here })[0];
  if (!nested) throw new Error(`${name} is not installed (npm install)`);
  return join(here, nested);
};
const version = (/** @type {string} */ name) => JSON.parse(readFileSync(join(pkg(name), "package.json"), "utf8")).version;

mkdirSync(out, { recursive: true });
copyFileSync(join(pkg("elkjs"), "lib/elk.bundled.js"), join(out, "elk.bundled.js"));
copyFileSync(join(pkg("cytoscape"), "dist/cytoscape.esm.min.mjs"), join(out, "cytoscape.mjs"));
copyFileSync(join(pkg("mermaid"), "dist/mermaid.min.js"), join(out, "mermaid.min.js"));
await build({
  stdin: { contents: 'export { default } from "cytoscape-fcose";', resolveDir: here },
  bundle: true, format: "esm", minify: true, legalComments: "eof", outfile: join(out, "fcose.mjs"), logLevel: "warning",
});

const libs = ["elkjs", "cytoscape", "cytoscape-fcose", "cose-base", "layout-base", "mermaid"];
const licenses = libs.map(n => {
  const dir = pkg(n);
  const meta = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  let text = "";
  for (const f of ["LICENSE", "LICENSE.md", "LICENSE.txt", "license"]) {
    try { text = readFileSync(join(dir, f), "utf8"); break; } catch { /* next */ }
  }
  return `${n} ${version(n)} (${meta.license})\n\n${text.trim() || "(no license file in the package)"}\n`;
});
writeFileSync(join(out, "LICENSES.txt"), "Third-party libraries in this directory, copied by vendor.mjs.\n\n" +
  licenses.join("\n" + "-".repeat(78) + "\n\n"));
console.log("vendor/:", libs.map(n => `${n}@${version(n)}`).join(" "));
