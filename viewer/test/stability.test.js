// The stability scores on hand-made layouts with known answers.

import { test } from "node:test";
import assert from "node:assert/strict";
import { compare } from "../js/stability.js";

/** @typedef {import("../types/nolli").Scene} Scene @typedef {import("../types/nolli").Laid} Laid @typedef {import("../types/nolli").Box} Box */

/** @param {[string, string | null][]} nodes id, parent @returns {Scene} */
const scene = nodes => /** @type {Scene} */ (/** @type {unknown} */ ({ nodes: nodes.map(([id, parent]) => ({ id, parent })) }));
/** @param {Record<string, [number, number, number?, number?]>} boxes x, y, w, h @returns {Laid} */
const laid = boxes => ({ width: 0, height: 0, edges: [], nodes: new Map(Object.entries(boxes).map(([id, [x, y, w = 10, h = 10]]) => [id, { x, y, w, h }])) });
/** @param {Laid} l @param {(b: Box) => Box} f */
const map = (l, f) => ({ ...l, nodes: new Map([...l.nodes].map(([id, b]) => [id, f(b)])) });

const flat = scene([["a", null], ["b", null], ["c", null], ["d", null], ["e", null]]);
const grid = laid({ a: [0, 0], b: [40, 0], c: [80, 0], d: [0, 40], e: [80, 40] });

test("the same layout keeps everything", () => {
  assert.deepEqual(compare(grid, grid, flat, flat), { n: 5, order: 1, knn: 1, residual: 0 });
});

test("a shift and a uniform scale change nothing", () => {
  const moved = map(grid, b => ({ x: 3 * b.x + 500, y: 3 * b.y - 70, w: 3 * b.w, h: 3 * b.h }));
  const r = compare(grid, moved, flat, flat);
  assert.equal(r.order, 1); assert.equal(r.knn, 1); assert.ok(r.residual < 1e-12);
});

test("a mirror on both axes keeps no strict order", () => {
  const mirrored = map(grid, b => ({ ...b, x: -b.x - b.w, y: -b.y - b.h }));
  const r = compare(grid, mirrored, flat, flat);
  // every strict left/right and above/below relation flips; only "overlapping" (a/b/c share a row) stays
  assert.ok(r.order < 0.5, `order ${r.order}`);
  assert.equal(r.knn, 1);  // distances are the same
});

test("swapping two boxes breaks only their relations", () => {
  const swapped = laid({ a: [40, 0], b: [0, 0], c: [80, 0], d: [0, 40], e: [80, 40] });
  const r = compare(grid, swapped, flat, flat);
  assert.ok(r.order > 0.7 && r.order < 1, `order ${r.order}`);
  assert.ok(r.residual > 0);
});

test("boxes that are new after the step don't count", () => {
  const grown = laid({ a: [0, 0], b: [40, 0], c: [80, 0], d: [0, 40], e: [80, 40], x: [500, 500] });
  assert.deepEqual(compare(grid, grown, flat, scene([...flat.nodes.map(n => /** @type {[string, null]} */ ([n.id, null])), ["x", "a"]])), { n: 5, order: 1, knn: 1, residual: 0 });
});

test("a container and what it contains are not compared", () => {
  const nested = scene([["box", null], ["in", "box"], ["out", null]]);
  const before = laid({ box: [0, 0, 100, 100], in: [10, 30], out: [200, 0] });
  const after = laid({ box: [0, 0, 100, 100], in: [80, 80], out: [200, 0] });  // "in" moves inside its box
  assert.equal(compare(before, after, nested, nested).order, 1);
});
