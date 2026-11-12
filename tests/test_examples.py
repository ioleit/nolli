"""The examples in dev/examples/ run against nolli as it is (they show the library's use)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_paths_between_operations_and_store():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/paths_between.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("flowchart TD")
    assert "n_repo_nolli_operations_py" in out and "n_repo_nolli_store_maps_py" in out
    assert "n_repo_nolli_cli_py" not in out  # nothing that is not on a path


def test_layer_skips_past_operations():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/layer_skips.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert "nolli/server/serve.py\n" in out and "-> nolli/logic/transformations/projection/__init__.py: " in out
    assert "-> nolli/operations.py" not in out and "-> nolli/model/" not in out


def test_propose_package_groups_the_layers_above_logic():
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/propose_package.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("flowchart TD")
    assert 'n_repo_nolli_app["app/"]' in out and "n_repo_nolli_logic" in out
    assert "n_repo_nolli_cli_py" not in out and "n_repo_nolli_server" not in out  # folded into app/
    assert "n_repo_dev" not in out and "n_repo_viewer" not in out  # nothing outside nolli/


def test_custom_overlay_kept_and_read_back(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/custom_overlay.py"), str(ROOT), str(tmp_path)], capture_output=True, text=True, check=True).stdout
    assert "live: repo:nolli/model/scene.py: cool node, bro" in out
    assert "cool folded node comprising" in out and "cool edge, bro" in out
    assert "kept for the projection: ['cool']" in out and any(tmp_path.glob("local/**/overlays/cool.json"))  # kept in the data directory given
    assert out.rstrip().endswith("live and kept agree on the scene and the module-level projection")


def test_annotations_kept_on_a_projection(tmp_path):
    given = tmp_path / "notes.json"
    given.write_text(json.dumps({"repo:nolli/model": {"text": "the schema"},
                                 "use|repo:nolli/server|repo:nolli/logic|import": {"text": "a line between folded boxes"}}))
    data = tmp_path / "data"
    out = subprocess.run([sys.executable, str(ROOT / "dev/examples/annotations.py"), str(ROOT), str(given), str(data)], capture_output=True, text=True, check=True).stdout
    assert f"projected model: {ROOT}@worktree~project:" in out and f"projected from:  {ROOT}@worktree\n" in out
    assert len(list(data.glob("local/**/overlays/annotations.json"))) == 1  # kept in the data directory given
    assert "use|repo:nolli/server|repo:nolli/logic|import: a line between folded boxes   (only in the projection)" in out
    assert "repo:nolli/model: the schema\n" in out
    assert out.rstrip().endswith("the annotations were kept on the projected model and read back")
