// Where the fixture server (serve_fixture.py) writes the key of each fixture's model, by the
// name of its dataset directory, and how a spec opens one: the server keeps models by key, so a
// page names the model by its key (?t=<key>), as `nolli view DIR` does after importing DIR.

import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const PORT = 8799;
export const KEYS = join(tmpdir(), `nolli-e2e-${PORT}-keys.json`);

/** Return the page's path for the fixture whose dataset directory is ``name`` (ledger-review, tasks). @param {string} name */
export function pathOf(name) {
  const keys = JSON.parse(readFileSync(KEYS, "utf8"));
  if (!keys[name]) throw new Error(`no fixture ${name} in ${KEYS}: ${Object.keys(keys).join(", ")}`);
  return "/?t=" + encodeURIComponent(keys[name]);
}
