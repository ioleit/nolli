"""The made-up review the browser tests use (fixtures/review_repo.py) still builds, and its
verdicts still pass nolli agent check: a change to the diagram that moves an edge shows up
here, before the screenshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nolli.cli import main
from nolli.model import Model
from nolli.review import read_verdicts, resolve_verdicts
from nolli.scene import ViewState, scene

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import review_repo  # noqa: E402


def test_review_fixture(tmp_path, capsys):
    ds = review_repo.build(tmp_path)
    capsys.readouterr()
    assert main(["agent", "check", str(ds)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "verdicts": 8, "blunder": 1, "problem": 1, "question": 3,
                                                    "brilliant": 0, "good": 2, "fine": 1, "errors": 0}


def test_verdict_on_a_bundled_edge(tmp_path):
    """Going to the verdict on client.ts ==> GET /alerts/overdue unfolds the API, whose endpoints
    client.ts calls are then drawn as one edge to the API's box: the verdict goes on that edge,
    not on client.ts."""
    ds = review_repo.build(tmp_path)
    m = Model.load(ds / "model.json")
    verdicts = read_verdicts((ds / "verdicts.json").read_text())
    v = next(r for r in resolve_verdicts(m, verdicts) if r["target"].startswith("n_fs_web_src_client_ts ==>"))
    s = scene(m, ViewState(expanded=v["reveal"] + ["fs:.", "fs:web/src"], groups=["api"]), verdicts)
    edge = next(e for e in s["edges"] if e["src"] == "fs:web/src/client.ts" and e["kind"] == "use" and e["subkind"] == "http")
    assert edge["dst"] == "api:http:ledger/api/app.py:app"
    assert edge["verdict"] == "question"
    assert next(n for n in s["nodes"] if n["id"] == "fs:web/src/client.ts")["verdict"] is None
