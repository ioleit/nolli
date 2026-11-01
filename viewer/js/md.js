// The one place nolli's Markdown becomes something to look at: the hover cards and the
// review panel. Descriptions (nolli/prose.py) and an agent's reasons use a small subset:
//
//   paragraphs, one per sentence, between blank lines
//   `code spans`             the data in a sentence (names, counts), set apart
//   > a quoted paragraph     what the code says about itself (a docstring)
//   [problem](verdict:problem)  a verdict's mark (a link to a made-up scheme)
//   \* backslash escapes
//
// Everything else stays text as written. Text goes in as text (textContent), never as HTML:
// names come from the repository.

import { markHTML } from "./verdicts.js";

const PUNCT = /[!-/:-@[-`{-~]/;
const VERDICT = /^\[[^\]\n]*\]\(verdict:([a-z]+)\)/;

/** @param {string} md @returns {string[]} the paragraphs, each on one line */
export function paragraphsOf(md) {
  return md ? md.split(/\n\s*\n/).map(p => p.trim().replace(/\s+/g, " ")).filter(Boolean) : [];
}

/** Markdown on one line, as one paragraph (as prose.one_line). @param {string} text */
export const oneLine = text => text.trim().replace(/\s+/g, " ");

/** @typedef {{ text: string } | { code: string } | { verdict: string }} Span a piece of a paragraph */

/** One paragraph's inline content as pieces. @param {string} text @returns {Span[]} */
export function inline(text) {
  /** @type {Span[]} */
  const out = [];
  let plain = "";
  const flush = () => { if (plain) out.push({ text: plain }); plain = ""; };
  for (let i = 0; i < text.length;) {
    const c = text[i];
    if (c === "\\" && PUNCT.test(text[i + 1] || "")) { plain += text[i + 1]; i += 2; continue; }
    if (c === "`") {
      let n = 1; while (text[i + n] === "`") n++;
      const end = findRun(text, i + n, n);
      if (end < 0) { plain += text.slice(i, i + n); i += n; continue; }
      let inner = text.slice(i + n, end);
      if (inner.length > 2 && inner[0] === " " && inner.at(-1) === " " && inner.trim()) inner = inner.slice(1, -1);
      flush(); out.push({ code: inner });
      i = end + n; continue;
    }
    const m = c === "[" ? VERDICT.exec(text.slice(i)) : null;
    if (m) { flush(); out.push({ verdict: m[1] }); i += m[0].length; continue; }
    plain += c; i++;
  }
  flush();
  return out;
}

/** Append one paragraph's inline content to `el`. @param {Node} el @param {string} text */
export function appendInline(el, text) {
  for (const sp of inline(text)) {
    if ("text" in sp) { el.appendChild(document.createTextNode(sp.text)); continue; }
    const s = document.createElement("span");
    if ("code" in sp) { s.className = "v"; s.textContent = sp.code; } else { s.className = "vd " + sp.verdict; s.innerHTML = markHTML(sp.verdict); }
    el.appendChild(s);
  }
}

/** Where a backtick run of exactly `n` starts, from `from` on; -1 if none. @param {string} text @param {number} from @param {number} n */
function findRun(text, from, n) {
  const re = /`+/g; re.lastIndex = from;
  for (let m; (m = re.exec(text));) if (m[0].length === n) return m.index;
  return -1;
}

/** Markdown as paragraphs (<p>), a quoted one as <p><q class="doc">. @param {string} md */
export function render(md) {
  const f = document.createDocumentFragment();
  for (const para of paragraphsOf(md)) {
    const p = document.createElement("p");
    if (para.startsWith(">")) {
      const q = document.createElement("q"); q.className = "doc"; appendInline(q, para.replace(/^>\s?/, "")); p.appendChild(q);
    } else appendInline(p, para);
    f.appendChild(p);
  }
  return f;
}

/** One paragraph's inline content as HTML, for markup built as strings. @param {string} text */
export function inlineHTML(text) {
  const s = document.createElement("span");
  appendInline(s, oneLine(text));
  return s.innerHTML;
}
