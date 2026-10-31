"""Builds the review fixture (tests/fixtures/review_repo.py) in a temporary directory and
serves the viewer on it, for the Playwright tests: python e2e/serve_fixture.py <port>."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "viewer"), str(ROOT / "tests" / "fixtures")]

import review_repo  # noqa: E402
from serve import make_server  # noqa: E402

with tempfile.TemporaryDirectory(prefix="nolli-e2e-") as tmp:
    ds = review_repo.build(Path(tmp))
    srv = make_server(ds.parent, port=int(sys.argv[1]))
    print(f"serving {ds.name} on http://127.0.0.1:{sys.argv[1]}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
