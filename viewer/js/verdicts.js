// The review agent's verdicts: their marks and their order. The marks are chess annotations:
// ?? a blunder, ? a mistake, ?! a dubious move, ! a good one, !! a brilliant one; ✓ is fine.
// nolli/review.py has the same order (VERDICTS).

/** @typedef {import("../types/nolli").Verdict} Verdict */

/** Most attention first: the panel's order, and which mark a box or edge with several verdicts shows. @type {Verdict[]} */
export const ORDER = ["blunder", "problem", "question", "brilliant", "good", "fine"];

/** @type {Record<Verdict, string>} */
const GLYPH = { blunder: "??", problem: "?", question: "?!", brilliant: "!!", good: "!", fine: "✓" };

/** Higher asks for more attention. */
export const RANK = /** @type {Record<Verdict, number>} */ (Object.fromEntries(ORDER.map((v, i) => [v, ORDER.length - 1 - i])));

/** The mark of a verdict, "•" for one this viewer does not know. @param {string} v */
export const glyph = v => GLYPH[/** @type {Verdict} */ (v)] ?? "•";
