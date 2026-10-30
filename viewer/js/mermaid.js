// The static Mermaid export (default.mmd, written by nolli extract) in an overlay pane.

const pane = document.getElementById("mmdpane");

export async function toggleMermaid(dataset) {
  if (pane.style.display === "block") { pane.style.display = "none"; return; }
  pane.style.display = "block"; pane.innerHTML = "rendering…";
  const src = await (await fetch(`/data/${dataset}/default.mmd`)).text();
  try {
    const mermaid = (await import("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs")).default;
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose", maxTextSize: 200000 });
    const { svg } = await mermaid.render("mmd" + Date.now(), src);
    pane.innerHTML = `<div style="position:sticky;top:0;background:Canvas;padding:4px;color:var(--muted)">default.mmd (static export of the default cut) · click to close</div>` + svg;
  } catch (e) { pane.innerHTML = `<pre style="color:#c33">${String(e)}</pre><pre>${src.replace(/</g, "&lt;")}</pre>`; }
}

pane.addEventListener("click", ev => { if (ev.target.closest("div[style*=sticky]")) ev.currentTarget.style.display = "none"; });
