// Browser tests of the review view (e2e/): a made-up repository with a reviewed change
// (tests/fixtures/review_repo.py), served by e2e/serve_fixture.py. Python comes from
// $NOLLI_PYTHON, else the clone's .venv, else python3 (it needs nolli's dependencies);
// Chromium from $NOLLI_CHROMIUM, else /usr/bin/chromium if there is one, else Playwright's own.

import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const PORT = 8799;
const python = process.env.NOLLI_PYTHON || (existsSync("../.venv/bin/python") ? "../.venv/bin/python" : "python3");
const chromium = process.env.NOLLI_CHROMIUM || (existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined);

export default defineConfig({
  testDir: "e2e",
  // the screenshots live next to the specs, one folder per spec file, without a platform suffix:
  // fonts differ between systems, so they are made and compared on one (README)
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}{ext}",
  fullyParallel: true,
  reporter: [["list"]],
  expect: { toHaveScreenshot: { animations: "disabled", caret: "hide" } },  // exact: a verdict glyph is ~30 pixels
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1400, height: 900 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    colorScheme: "light",
    launchOptions: chromium ? { executablePath: chromium } : {},
  },
  webServer: { command: `${python} e2e/serve_fixture.py ${PORT}`, url: `http://127.0.0.1:${PORT}/api/list`, timeout: 60_000 },
});
