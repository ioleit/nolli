"""The made-up change of the navigation tests (fixtures/nav_repo.py): its verdict, on an edge
the review diagram draws on its own, stays on an edge however the viewer rolls that edge up:
into the folders around it, into a fan bundle, or not at all."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helpers import draw
from nolli.operations import _open_change_scene
from nolli.cli import main
from nolli.store import DirectoryStore

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
    (["repo:tasks/jobs", "repo:tasks/store"], ("repo:tasks/jobs/worker.py", "repo:tasks/store/users")),  # users folded
    (["repo:tasks/jobs", "repo:tasks/store", "repo:tasks/store/users"], ("repo:tasks/jobs/worker.py", "repo:tasks/store/users/repo.py")),
])
def test_a_verdict_follows_its_edge_when_rolled_up(dataset, unfolded, edge):
    store = DirectoryStore(dataset.parent)
    sc = draw(_open_change_scene(store, store.find_key(dataset.name)), expanded=["repo:", "repo:tasks", *unfolded])
    assert [(e["src"], e["dst"]) for e in sc["edges"] if "V1" in e["verdicts"]["verdicts"]] == [edge]
    assert not [n["id"] for n in sc["nodes"] if n["verdicts"]["verdicts"]]
