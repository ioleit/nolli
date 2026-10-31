"""The made-up review the browser tests use (fixtures/review_repo.py) still builds, and its
verdicts still pass nolli agent check: a change to the diagram that moves an edge shows up
here, before the screenshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nolli.cli import main

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import review_repo  # noqa: E402


def test_review_fixture(tmp_path, capsys):
    ds = review_repo.build(tmp_path)
    capsys.readouterr()
    assert main(["agent", "check", str(ds)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "verdicts": 7, "problem": 1, "question": 3, "fine": 3, "errors": 0}
