// The scene as JSON instead of a drawing, for working on the server side.

const pane = document.getElementById("rawpane");
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

export function showRaw(scene) {
  pane.style.display = "block";
  const foldable = new Set(scene.nodes.filter(n => n.foldable).map(n => n.id));
  // node ids that can be unfolded or folded are clickable, as boxes are in the drawing
  const html = esc(JSON.stringify(scene, null, 1)).replace(/("id": )"([^"]*)"/g, (all, k, id) => foldable.has(id) ? `${k}"<span class="unfold" data-id="${id}">${id}</span>"` : all);
  pane.innerHTML = `<div class="req">GET <a href="${esc(scene.url)}" target="_blank">${esc(scene.url)}</a> · ${scene.stats.nodes} nodes · ${scene.stats.edges} edges · scene ${scene.stats.ms.scene} ms · round trip ${scene.stats.ms.roundtrip} ms · click an id to unfold/fold it</div><pre>${html}</pre>`;
}

export function hideRaw() { pane.style.display = "none"; }

export function onRawToggle(fn) {
  pane.addEventListener("click", ev => { const t = ev.target.closest(".unfold"); if (t) fn(t.dataset.id); });
}
