// Layout: scene -> positions, by one of several engines. No DOM.
//
// Every engine returns { width, height, nodes, edges }: nodes maps id -> { x, y, w, h }
// (absolute, top-left corner); edges lists { edge, sections }, each section a list of
// absolute points. draw.js only needs that shape.

export const ENGINES = {
  elk: { label: "layered (ELK)", load: () => import("./elk.js") },
  fcose: { label: "force (fCoSE)", load: () => import("./fcose.js") },
};

// opts: { engine, direction, seed }
export async function layout(scene, opts) {
  const engine = ENGINES[opts.engine] || ENGINES.elk;
  return (await engine.load()).layout(scene, opts);
}

// ---------- shared by engines ----------

const textW = (s, icon) => Math.max(56, s.length * 6.4 + 22 + (icon ? 16 : 0));

// the size a folded node needs for its label, badge and chips
export function leafSize(n) {
  if (n.kind === "anchor") return { w: 14, h: 14 };
  const sub = n.leaves ? "+" + n.leaves : "";
  return {
    w: Math.max(textW(n.label + sub, n.icon), (n.badge ? n.badge.length * 5.4 : 0) + n.chips.length * 11 + 20),
    h: (n.badge || n.chips.length) ? 40 : 30,
  };
}

// room around a container's children: the header with title, badges and − sits on top
export const CONTAINER_PAD = { top: 30, left: 10, bottom: 10, right: 10 };

export function childrenOf(scene) {
  const kids = new Map();
  for (const n of scene.nodes) { const k = n.parent || "ROOT"; if (!kids.has(k)) kids.set(k, []); kids.get(k).push(n); }
  return kids;
}
