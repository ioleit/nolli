// The Mermaid view: the static export (default.mmd, written by nolli extract), rendered once per dataset.

import { byId } from "./dom.js";

const pane = byId("mmdpane");
let rendered = "";

/** @param {string} dataset */
export async function showMermaid(dataset) {
  pane.style.display = "block";
  if (rendered === dataset) return;
  rendered = dataset; pane.innerHTML = "rendering…";
  const src = await (await fetch(`/data/${dataset}/default.mmd`)).text();
  try {
    const mermaid = (await import("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs")).default;
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose", maxTextSize: 200000 });
    const { svg } = await mermaid.render("mmd" + Date.now(), src);
    if (rendered === dataset) pane.innerHTML = `<div class="req">default.mmd: the static export of the default cut, written by nolli extract; the controls don't apply</div>` + svg;
  } catch (e) { pane.innerHTML = `<pre style="color:#c33">${String(e)}</pre><pre>${src.replace(/</g, "&lt;")}</pre>`; }
}

export function hideMermaid() { pane.style.display = "none"; }
