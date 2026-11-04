"""The made-up review the browser tests use (fixtures/review_repo.py) still builds, and its
verdicts still pass nolli agent check: a change to the diagram that moves an edge shows up
here, before the screenshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from helpers import draw, find_node
from nolli.datasets import open_change_scene
from nolli.cli import main
from nolli.review.verdicts import describe_verdicts
from nolli.store import DirectoryStore

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import review_repo  # noqa: E402


def test_review_fixture(tmp_path, capsys):
    ds = review_repo.build(tmp_path)
    capsys.readouterr()
    assert main(["agent", "check", str(ds)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "verdicts": 8, "blunder": 1, "problem": 1, "question": 3,
                                                    "brilliant": 0, "good": 2, "fine": 1, "errors": 0}


def test_verdict_on_a_bundled_edge(tmp_path):
    """Going to the verdict on client.ts ==> GET /alerts/overdue unfolds the API; with at most two
    lines between two boxes, the endpoints client.ts calls are drawn as one edge to the API's
    box: the verdict goes on that edge, not on client.ts."""
    ds = review_repo.build(tmp_path)
    store = DirectoryStore(ds.parent)
    sc = open_change_scene(store, store.find_key(ds.name))
    verdicts = sc.overlays["verdicts"].load(sc)  # the review kept with the dataset
    v = next(r for r in describe_verdicts(sc, verdicts) if r["target"].startswith("n_repo_web_src_client_ts ==>"))
    s = draw(sc, expanded=v["reveal"] + ["repo:", "repo:web/src"], groups=["api:"], max_lines=2)
    edge = next(e for e in s["edges"] if e["src"] == "repo:web/src/client.ts" and e["kind"] == "use" and e["subkind"] == "http")
    assert edge["dst"] == "api:http:ledger/api/app.py:app"
    assert edge["verdicts"]["verdict"] == "question"
    assert find_node(s, "repo:web/src/client.ts")["verdicts"]["verdict"] is None
