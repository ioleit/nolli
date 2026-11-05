"""The examples in dev/examples/ run against nolli as it is (they show the library's use)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_paths_from_datasets_to_store():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/paths.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("flowchart TD")
    assert "n_repo_nolli_datasets_py" in out and "n_repo_nolli_store_maps_py" in out
    assert "n_repo_nolli_cli_py" not in out  # nothing that is not on a path


def test_skips_past_datasets_build_scenes():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/skips.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert "nolli/server/serve.py\n" in out and "-> nolli/transformations/projection.py: " in out
    assert "-> nolli/datasets.py" not in out and "-> nolli/model/" not in out
