// The ELK global, from elk.bundled.js in a script tag (index.html, bench.html), typed
// from the elkjs npm package (a dev dependency).

import type ElkConstructor from "elkjs/lib/elk.bundled.js";

declare global {
  const ELK: typeof ElkConstructor;
}
