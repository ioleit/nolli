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
    const mermaid = await loadMermaid();
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose", maxTextSize: 200000 });
    const { svg } = await mermaid.render("mmd" + Date.now(), src);
    if (rendered === dataset) pane.innerHTML = `<div class="req">default.mmd: the static export of the default cut, written by nolli extract; the controls don't apply</div>` + svg;
  } catch (e) { pane.innerHTML = `<pre style="color:#c33">${String(e)}</pre><pre>${src.replace(/</g, "&lt;")}</pre>`; }
}

/** Mermaid (vendor/mermaid.min.js, 3.5 MB), loaded on first use: a classic script that sets globalThis.mermaid. */
function loadMermaid() {
  if (globalThis.mermaid) return Promise.resolve(globalThis.mermaid);
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "vendor/mermaid.min.js";
    s.onload = () => resolve(globalThis.mermaid);
    s.onerror = () => reject(new Error("could not load vendor/mermaid.min.js"));
    document.head.appendChild(s);
  });
}

export function hideMermaid() { pane.style.display = "none"; }
