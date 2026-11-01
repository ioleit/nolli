"""Reviews in the viewer's server (viewer/reviews.py, /api/review): where the verdicts came from,
whether a review agent can run, a run's states through start, cancel and the end, uploads
where they are stored and refused where they are not, and verdicts read from nolli's CI
comments by nolli serve. The agent itself (nolli/reviewer.py) is replaced: it needs the API."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nolli import reviewer
from nolli.viewer import hosted, serve
from test_ci import bot_comment, comment, dataset  # noqa: F401  # the fixture


def call(srv, path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method), timeout=10) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


@pytest.fixture
def server(dataset):  # noqa: F811
    srv = serve.make_server(dataset.parent, port=0, repos={dataset.name: dataset})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def until(cond, seconds: float = 10) -> None:
    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        time.sleep(0.05)
    assert cond()


def test_without_a_key(server, dataset, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, st = call(server, f"/api/review?d={dataset.name}")
    assert status == 200 and st["verdicts"] is None and st["run"] is None
    assert (st["can_run"], st["why_not"], st["uploads"], st["server"]) == (False, "key", True, "view")
    assert call(server, f"/api/review?d={dataset.name}", "POST")[0] == 409
    assert call(server, "/api/review?d=nope")[0] == 404


def test_upload_says_where_the_verdicts_came_from(server, dataset):  # noqa: F811
    v = [{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]
    assert call(server, f"/api/verdicts?d={dataset.name}", "POST", json.dumps(v).encode()) == (200, {"saved": 1})
    assert call(server, f"/api/review?d={dataset.name}")[1]["verdicts"]["by"] == "upload"


def test_a_run_its_cancel_and_its_end(server, dataset, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(reviewer, "available", lambda: None)
    go = threading.Event()

    def fake(ds: Path, repo: Path, out: Path, on_step, cancel: threading.Event, effort: str = "") -> reviewer.Run:
        run = reviewer.Run(turns=2)
        on_step("reading shop/cache.py", run)
        while not go.is_set():
            if cancel.is_set():
                raise reviewer.Cancelled()
            time.sleep(0.02)
        out.mkdir(parents=True, exist_ok=True)
        (out / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "fine", "reason": "As intended."}]))
        return run

    monkeypatch.setattr(reviewer, "review", fake)
    status, st = call(server, f"/api/review?d={dataset.name}", "POST")
    assert status == 200 and st["can_run"] and st["run"]["state"] in ("queued", "running")
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["step"] == "reading shop/cache.py")
    assert call(server, f"/api/review?d={dataset.name}", "POST")[1]["run"]["state"] == "running"  # one run at a time
    call(server, f"/api/review/cancel?d={dataset.name}", "POST")
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["state"] == "cancelled")
    assert not (dataset / "verdicts.json").exists()

    call(server, f"/api/review?d={dataset.name}", "POST")
    go.set()
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["state"] == "done")
    st = call(server, f"/api/review?d={dataset.name}")[1]
    assert st["verdicts"]["by"] == "agent" and st["verdicts"]["turns"] == 2
    assert json.loads((dataset / "verdicts.json").read_text())[0]["verdict"] == "fine"


def test_a_failed_run_keeps_the_verdicts_shown(server, dataset, monkeypatch):  # noqa: F811
    monkeypatch.setattr(reviewer, "available", lambda: None)
    (dataset / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]))

    def fails(*args, **kwargs):
        raise reviewer.Failed("the agent wrote no verdicts")

    monkeypatch.setattr(reviewer, "review", fails)
    call(server, f"/api/review?d={dataset.name}", "POST")
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["state"] == "failed")
    st = call(server, f"/api/review?d={dataset.name}")[1]
    assert st["run"]["error"] == "the agent wrote no verdicts" and st["verdicts"]["by"] == "unknown"
    assert json.loads((dataset / "verdicts.json").read_text())[0]["verdict"] == "good"


def test_serve_reads_the_ci_review_and_stores_no_uploads(tmp_path, dataset, capsys, monkeypatch):  # noqa: F811
    (dataset / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]))
    text = comment(dataset, capsys)
    (dataset / "verdicts.json").unlink()
    site = hosted.Site(tmp_path / "cache")
    ds = site.data / dataset.name
    ds.mkdir()
    for f in dataset.iterdir():
        (ds / f.name).write_bytes(f.read_bytes())
    report = json.loads((ds / "report.json").read_text())  # as the model says: built from '#7' on GitHub
    report["meta"]["diff"] |= {"remote": "https://github.com/me/shop", "pr": 7}
    (ds / "report.json").write_text(json.dumps(report))
    asked = []

    def api(path, tok):
        asked.append(path)
        return [bot_comment(text)]

    monkeypatch.setattr(hosted.github, "api", api)
    info = site.ci_review(ds)
    assert asked == ["repos/me/shop/issues/7/comments?per_page=100"] and info["found"] == "verdicts"
    assert json.loads((ds / "review.json").read_text())["by"] == "ci" and json.loads((ds / "verdicts.json").read_text())[0]["verdict"] == "good"
    assert site.ci_review(ds) is info and len(asked) == 1  # asked once a minute at most

    srv = hosted.make_server(tmp_path / "cache", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(srv, f"/api/verdicts?d={ds.name}", "POST", b"[]")[0] == 405
        st = call(srv, f"/api/review?d={ds.name}")[1]
        assert st["server"] == "serve" and st["uploads"] is False and st["verdicts"]["by"] == "ci" and st["ci"]["found"] == "verdicts"
    finally:
        srv.shutdown()
        srv.server_close()
