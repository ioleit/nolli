"""The made-up change of the navigation tests (fixtures/nav_repo.py): its verdict, on an edge
the review diagram draws on its own, stays on an edge however the viewer rolls that edge up:
into the folders around it, into a fan bundle, or not at all."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nolli.cli import main
from nolli.model import Model
from nolli.review import read_verdicts
from nolli.scene import ViewState, scene

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import nav_repo  # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    return nav_repo.build(tmp_path_factory.mktemp("nav"))


def test_verdicts_pass_the_check(dataset, capsys):
    capsys.readouterr()
    assert main(["agent", "check", str(dataset)]) == 0


@pytest.mark.parametrize("unfolded, edge", [
    ([], ("repo:tasks/jobs", "repo:tasks/store")),  # both folders folded
    (["repo:tasks/jobs", "repo:tasks/store"], ("repo:tasks/jobs/worker.py", "repo:tasks/store")),  # bundled into store
    (["repo:tasks/jobs", "repo:tasks/store", "repo:tasks/store/users"], ("repo:tasks/jobs/worker.py", "repo:tasks/store/users/repo.py")),
])
def test_a_verdict_follows_its_edge_when_rolled_up(dataset, unfolded, edge):
    m = Model.load(dataset / "model.json")
    sc = scene(m, ViewState(expanded=["repo:", "repo:tasks", *unfolded]), read_verdicts((dataset / "verdicts.json").read_text()))
    assert [(e["src"], e["dst"]) for e in sc["edges"] if "V1" in e["verdicts"]] == [edge]
    assert not [n["id"] for n in sc["nodes"] if n["verdicts"]]
