// The Markdown subset of the hover cards (js/md.js), on text as nolli/prose.py writes it.

import { test } from "node:test";
import assert from "node:assert/strict";
import { inline, paragraphsOf } from "../js/md.js";

test("values are code spans, escapes are text, verdicts are marks", () => {
  assert.deepEqual(inline("`api/` references `Store` in `store` [problem](verdict:problem)"), [
    { code: "api/" }, { text: " references " }, { code: "Store" }, { text: " in " }, { code: "store" }, { text: " " }, { verdict: "problem" }]);
  assert.deepEqual(inline("`pkg`'s \\_\\_init\\_\\_.py defines"), [{ code: "pkg" }, { text: "'s __init__.py defines" }]);
  assert.deepEqual(inline("a [link](https://x) and a lone ` tick"), [{ text: "a [link](https://x) and a lone ` tick" }]);
});

test("paragraphs are split at blank lines and kept on one line", () => {
  assert.deepEqual(paragraphsOf("one\n\n> two\nlines\n\n\nthree"), ["one", "> two lines", "three"]);
  assert.deepEqual(paragraphsOf(""), []);
});
