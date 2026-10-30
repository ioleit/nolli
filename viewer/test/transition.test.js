// Planning a step: where boxes start and end, and how far the view shifts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { ease, lerpBox, plan, rebase } from "../js/transition.js";

/** @typedef {import("../types/nolli").Scene} Scene @typedef {import("../types/nolli").Laid} Laid */

/** @param {[string, string | null][]} nodes @returns {Scene} */
const scene = nodes => /** @type {Scene} */ (/** @type {unknown} */ ({ nodes: nodes.map(([id, parent]) => ({ id, parent })) }));
/** @param {Record<string, [number, number, number, number]>} boxes @returns {Laid} */
const laid = boxes => ({ width: 0, height: 0, edges: [], nodes: new Map(Object.entries(boxes).map(([id, [x, y, w, h]]) => [id, { x, y, w, h }])) });

// "p" is unfolded: it grows from 100x30 to a container with children c1, c2; "q" is pushed right
const before = { s: scene([["p", null], ["q", null]]), l: laid({ p: [0, 0, 100, 30], q: [150, 0, 80, 30] }) };
const after = { s: scene([["p", null], ["c1", "p"], ["c2", "p"], ["q", null]]),
  l: laid({ p: [20, 10, 300, 200], c1: [30, 50, 100, 30], c2: [170, 120, 100, 30], q: [400, 10, 80, 30] }) };

test("rebase keeps the relative position inside the box", () => {
  const r = rebase({ x: 30, y: 50, w: 100, h: 30 }, { x: 20, y: 10, w: 300, h: 200 }, { x: 0, y: 0, w: 100, h: 30 });
  const want = { x: 10 / 3, y: 6, w: 100 / 3, h: 4.5 };
  for (const k of /** @type {const} */ (["x", "y", "w", "h"])) assert.ok(Math.abs(r[k] - want[k]) < 1e-9, `${k}: ${r[k]} vs ${want[k]}`);
});

test("unfolding: children start inside the clicked box's old rectangle and fade in", () => {
  const p = plan(before.s, before.l, after.s, after.l, "p");
  const c1 = p.nodes.get("c1"), c2 = p.nodes.get("c2");
  assert.ok(c1 && c2);
  assert.equal(c1.fade, "in");
  for (const m of [c1, c2]) {
    assert.ok(m.from.x >= 0 && m.from.x + m.from.w <= 100 + 1e-9, "inside horizontally");
    assert.ok(m.from.y >= 0 && m.from.y + m.from.h <= 30 + 1e-9, "inside vertically");
  }
  assert.deepEqual(p.nodes.get("q"), { from: { x: 150, y: 0, w: 80, h: 30 }, to: { x: 400, y: 10, w: 80, h: 30 }, fade: null });
});

test("unfolding: the view shifts by the clicked box's move, so it stays put on screen", () => {
  const p = plan(before.s, before.l, after.s, after.l, "p");
  assert.equal(p.anchor, "p");
  assert.deepEqual(p.shift, { x: -20, y: -10 });
});

test("folding is the reverse: children shrink into the folded box and fade out", () => {
  const p = plan(after.s, after.l, before.s, before.l, "p");
  const c1 = p.leaving.get("c1");
  assert.ok(c1);
  assert.equal(c1.fade, "out");
  assert.ok(c1.to.x >= 0 && c1.to.x + c1.to.w <= 100 + 1e-9 && c1.to.y >= 0 && c1.to.y + c1.to.h <= 30 + 1e-9);
  assert.equal(p.leaving.size, 2);
  assert.deepEqual(p.shift, { x: 20, y: 10 });
});

test("a new box without a drawn ancestor fades in where it ends; no click, no shift", () => {
  const p = plan(before.s, before.l, scene([["p", null], ["q", null], ["r", null]]), laid({ p: [0, 0, 100, 30], q: [150, 0, 80, 30], r: [300, 0, 50, 30] }));
  assert.deepEqual(p.nodes.get("r"), { from: { x: 300, y: 0, w: 50, h: 30 }, to: { x: 300, y: 0, w: 50, h: 30 }, fade: "in" });
  assert.equal(p.anchor, null);
  assert.deepEqual(p.shift, { x: 0, y: 0 });
});

test("easing and interpolation hit their ends", () => {
  assert.equal(ease(0), 0); assert.equal(ease(1), 1); assert.equal(ease(0.5), 0.5);
  const a = { x: 0, y: 0, w: 10, h: 10 }, b = { x: 10, y: 20, w: 30, h: 40 };
  assert.deepEqual(lerpBox(a, b, 0), a); assert.deepEqual(lerpBox(a, b, 1), b);
});
