// Routing around boxes, level by level, with the real libavoid (its Node build).

import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { AvoidLib } from "libavoid-js";
import { pieces, portOn, routeWith } from "../js/route/avoid.js";

/** @typedef {import("../types/nolli").Scene} Scene @typedef {import("../types/nolli").Laid} Laid @typedef {import("../types/nolli").Box} Box */

await AvoidLib.load(fileURLToPath(new URL("../node_modules/libavoid-js/dist/libavoid.wasm", import.meta.url)));
const A = AvoidLib.getInstance();

// P holds a, Q holds b, M (between them) holds m; edge a -> b must not cross M
/** @returns {Scene} */
const scene = () => /** @type {Scene} */ (/** @type {unknown} */ ({
  nodes: [
    { id: "P", parent: null, container: true }, { id: "a", parent: "P", container: false },
    { id: "M", parent: null, container: true }, { id: "m", parent: "M", container: false },
    { id: "Q", parent: null, container: true }, { id: "b", parent: "Q", container: false },
  ],
  edges: [{ key: "use|a|b|import", src: "a", dst: "b" }],
}));
/** @type {Record<string, [number, number, number, number]>} */
const B = { P: [0, 0, 300, 200], a: [100, 60, 100, 30], M: [60, 240, 180, 120], m: [90, 280, 60, 30], Q: [0, 400, 300, 200], b: [100, 480, 100, 30] };
/** @returns {Laid} */
const laid = () => ({ width: 300, height: 600, edges: [], nodes: new Map(Object.entries(B).map(([id, [x, y, w, h]]) => [id, { x, y, w, h }])) });

test("a port sits on the side facing the other end", () => {
  const c = { x: 0, y: 0, w: 100, h: 50 };
  assert.deepEqual(portOn(c, { x: 40, y: 300 }), { x: 40, y: 50 });   // below: bottom side
  assert.deepEqual(portOn(c, { x: 500, y: -90 }), { x: 92, y: 0 });   // above: top side, kept off the corner
  assert.deepEqual(portOn(c, { x: 400, y: 20 }), { x: 100, y: 20 });  // level with it: the right side
});

test("an edge between nested boxes is routed in one piece per level", () => {
  const ps = pieces(scene(), laid()).get("use|a|b|import");
  assert.deepEqual(ps?.map(p => p.level), ["P", "ROOT", "Q"]);
});

test("the route leaves and enters through the containers and goes around the one between", () => {
  const out = routeWith(A, scene(), laid());
  const pts = out.edges[0].sections[0];
  for (let i = 1; i < pts.length; i++) assert.ok(pts[i].x === pts[i - 1].x || pts[i].y === pts[i - 1].y, "orthogonal");
  const [m0, m1] = [B.M[0], B.M[0] + B.M[2]], [my0, my1] = [B.M[1], B.M[1] + B.M[3]];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const hit = Math.max(a.x, b.x) > m0 && Math.min(a.x, b.x) < m1 && Math.max(a.y, b.y) > my0 && Math.min(a.y, b.y) < my1;
    assert.ok(!hit, `segment ${JSON.stringify([a, b])} crosses M`);
  }
  assert.equal(pts[0].y, 90, "starts on a's bottom border");
  assert.equal(pts[pts.length - 1].y, 480, "ends on b's top border");
});

test("a box with a fractional centre is left vertically, not through its side", () => {
  // the case that once produced a slanted first segment: centre x = 977.5 + 57.2 / 2
  const sc = /** @type {Scene} */ (/** @type {unknown} */ ({ nodes: [{ id: "s", parent: null, container: false }, { id: "t", parent: null, container: false }], edges: [{ key: "k", src: "s", dst: "t" }] }));
  /** @type {Laid} */
  const l = { width: 0, height: 0, edges: [], nodes: new Map([["s", { x: 977.5, y: 131, w: 57.2, h: 30 }], ["t", { x: 400, y: 400, w: 60, h: 30 }]]) };
  const pts = routeWith(A, sc, l).edges[0].sections[0];
  for (let i = 1; i < pts.length; i++) assert.ok(Math.abs(pts[i].x - pts[i - 1].x) <= 0.5 || Math.abs(pts[i].y - pts[i - 1].y) <= 0.5, `segment ${i} slanted`);
  assert.ok(Math.abs(pts[0].y - 161) <= 0.5, `starts on the bottom border, not ${JSON.stringify(pts[0])}`);
});
