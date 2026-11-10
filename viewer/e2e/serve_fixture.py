"""Builds the test fixtures (tests/fixtures/: review_repo.py, nav_repo.py) in a temporary
directory and serves the viewer on their datasets, for the Playwright tests:
python e2e/serve_fixture.py <port>. A page opens one by its directory's name, the server running
in the data directory: /?t=ledger-review, /?t=tasks."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "fixtures")]

import nav_repo  # noqa: E402
import review_repo  # noqa: E402
from nolli.server.serve import make_local_server  # noqa: E402

with tempfile.TemporaryDirectory(prefix="nolli-e2e-") as tmp:
    data = review_repo.build(Path(tmp)).parent
    nav_repo.build(Path(tmp))
    os.chdir(data)  # a dataset directory named relative to it
    srv = make_local_server(data, None, port=int(sys.argv[1]))
    print(f"serving {', '.join(sorted(d.name for d in data.iterdir()))} on http://127.0.0.1:{sys.argv[1]}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
