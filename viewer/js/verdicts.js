// The review agent's verdicts: their marks and their order. The marks are chess annotations:
// ?? a blunder, ? a mistake, ?! a dubious move, !! a brilliant one; a good one is a thumbs up
// (chess.com's "excellent"), and ✓, in grey, is fine: nothing to look at.
// nolli/review.py has the same order (VERDICTS).

/** @typedef {import("../types/nolli").Verdict} Verdict */

/** Most attention first: the panel's order, and which mark a box or edge with several verdicts shows. @type {Verdict[]} */
export const ORDER = ["blunder", "problem", "question", "brilliant", "good", "fine"];

/** @type {Record<Verdict, string>} */
const GLYPH = { blunder: "??", problem: "?", question: "?!", brilliant: "!!", good: "", fine: "✓" };

/** Verdicts drawn as an icon instead of a glyph: a path in a 16 × 16 box. @type {Partial<Record<Verdict, string>>} */
export const ICON = { good: "M2 7h3v7H2z M6 7l2.5-4.5c.5-.9 2.1-.6 2 .7L10 6h3.2c1 0 1.7.9 1.5 1.9l-.9 4.7c-.2.8-.9 1.4-1.7 1.4H6z" };

/** Higher asks for more attention. */
export const RANK = /** @type {Record<Verdict, number>} */ (Object.fromEntries(ORDER.map((v, i) => [v, ORDER.length - 1 - i])));

/** The mark of a verdict, "•" for one this viewer does not know. @param {string} v */
export const glyph = v => GLYPH[/** @type {Verdict} */ (v)] ?? "•";

/** The mark of a verdict as HTML: its icon as inline SVG, or its glyph. @param {string} v */
export const markHTML = v => {
  const d = ICON[/** @type {Verdict} */ (v)];
  return d ? `<svg class="icon" viewBox="0 0 16 16" aria-label="${v}"><path d="${d}"/></svg>` : glyph(v);
};
