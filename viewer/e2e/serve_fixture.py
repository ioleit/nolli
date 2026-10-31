"""Builds the test fixtures (tests/fixtures/: review_repo.py, nav_repo.py) in a temporary
directory and serves the viewer on their datasets, for the Playwright tests:
python e2e/serve_fixture.py <port>."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "viewer"), str(ROOT / "tests" / "fixtures")]

import nav_repo  # noqa: E402
import review_repo  # noqa: E402
from serve import make_server  # noqa: E402

with tempfile.TemporaryDirectory(prefix="nolli-e2e-") as tmp:
    data = review_repo.build(Path(tmp)).parent
    nav_repo.build(Path(tmp))
    srv = make_server(data, port=int(sys.argv[1]))
    print(f"serving {', '.join(sorted(d.name for d in data.iterdir()))} on http://127.0.0.1:{sys.argv[1]}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
