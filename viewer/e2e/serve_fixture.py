"""Builds the test fixtures (tests/fixtures/: review_repo.py, nav_repo.py) in a temporary
directory, imports their datasets into the viewer's store, as nolli view does with a dataset
directory, and serves the viewer on them, for the Playwright tests:
python e2e/serve_fixture.py <port> <keys file>. The keys file maps each dataset directory's
name to its model's key (e2e/fixtures.js), and a page opens a fixture by that key: /?t=<key>."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "fixtures")]

import nav_repo  # noqa: E402
import review_repo  # noqa: E402
from nolli.cli import import_dataset  # noqa: E402
from nolli.logic.formats.dataset import read_key  # noqa: E402
from nolli.server.serve import make_local_server  # noqa: E402
from nolli.store.repo_directory import RepoStoreProvider  # noqa: E402

port, keys_file = int(sys.argv[1]), Path(sys.argv[2])
with tempfile.TemporaryDirectory(prefix="nolli-e2e-") as tmp:
    datasets = [review_repo.build(Path(tmp)), nav_repo.build(Path(tmp))]
    data = Path(tmp) / "store"
    stores = RepoStoreProvider(data)
    keys = {}
    for d in datasets:
        keys[d.name] = import_dataset(stores.open(read_key(d).repo), d).format_key()
    keys_file.write_text(json.dumps(keys, indent=2))
    srv = make_local_server(data, None, port=port)
    print(f"serving {', '.join(sorted(keys))} on http://127.0.0.1:{port}/ (keys in {keys_file})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
