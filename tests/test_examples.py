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


def test_logic_groups_four_packages():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/logic.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("flowchart TD")
    assert 'n_repo_nolli_logic["logic/"]' in out and "n_repo_nolli_datasets_py" in out
    assert "n_repo_nolli_extractors" not in out  # folded into logic/
    assert "n_repo_dev" not in out and "n_repo_viewer" not in out  # nothing outside nolli/


def test_cool_overlay_kept_and_read_back():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/cool.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert "live: repo:nolli/model/scene.py: cool node, bro" in out
    assert "cool folded node comprising" in out and "cool edge, bro" in out
    assert out.rstrip().endswith("live and kept agree on the scene and the module-level picture")
