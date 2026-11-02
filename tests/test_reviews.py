"""Reviews in the viewer's server (viewer/reviews.py, /api/review): where the verdicts came from,
whether a review agent can run, a run's states through start, cancel and the end, uploads
where they are stored and refused where they are not, and verdicts read from nolli's CI
comments by nolli serve. The agent itself (nolli/reviewer.py) is replaced: it needs the API."""

from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nolli import reviewer
from nolli.store import DirectoryStore, LocalRepository, Review
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
    srv = serve.make_server(dataset.parent, port=0, repos={dataset.name: dataset.parent.parent / "shop"})
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


def open_dataset(ds: Path):
    """The dataset's store, key and model."""
    store = DirectoryStore(ds.parent)
    key = store.find_key(ds.name)
    return store, key, store.load_model(key)


def test_serve_reads_the_ci_review_and_stores_no_uploads(tmp_path, dataset, capsys, monkeypatch):  # noqa: F811
    (dataset / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]))
    text = comment(dataset, capsys)
    (dataset / "verdicts.json").unlink()
    shutil.copytree(dataset, tmp_path / "cache" / "data" / dataset.name)
    site = hosted.Site(tmp_path / "cache")
    key = site.store.find_key(dataset.name)
    m = site.store.load_model(key)
    asked = []

    def api(path, tok):
        asked.append(path)
        return [bot_comment(text)]

    monkeypatch.setattr(hosted.github, "api", api)
    info = site.ci_review(key, m)
    assert asked == ["repos/me/shop/issues/7/comments?per_page=100"] and info["found"] == "verdicts"
    review = site.store.load_review(key)
    assert review.by["by"] == "ci" and review.verdicts[0]["verdict"] == "good"
    assert site.ci_review(key, m) is info and len(asked) == 1  # asked once a minute at most

    srv = hosted.make_server(tmp_path / "cache", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(srv, f"/api/verdicts?d={dataset.name}", "POST", b"[]")[0] == 405
        st = call(srv, f"/api/review?d={dataset.name}")[1]
        assert st["server"] == "serve" and st["uploads"] is False and st["verdicts"]["by"] == "ci" and st["ci"]["found"] == "verdicts"
    finally:
        srv.shutdown()
        srv.server_close()


def test_from_ci_without_github(dataset, capsys):  # noqa: F811
    from nolli.viewer.reviews import ci_pull, from_ci

    store, key, m = open_dataset(dataset)
    assert ci_pull(m) == ("me", "shop", 7)
    without = comment(dataset, capsys)  # a run without an agent: no verdicts in it
    (dataset / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]))
    reviewed = comment(dataset, capsys)
    (dataset / "verdicts.json").unlink()

    assert from_ci(store, key, m, []) == {"found": None, "applies": True}
    assert from_ci(store, key, m, [bot_comment(without)])["found"] == "no-review" and store.load_review(key) is None
    info = from_ci(store, key, m, [bot_comment(reviewed)])
    assert info["found"] == "verdicts" and info["dropped"] == 0
    assert store.load_review(key).by["by"] == "ci"
    store.save_review(key, Review(store.load_review(key).verdicts, {"by": "agent"}))  # a review run here since: kept
    from_ci(store, key, m, [bot_comment(reviewed)])
    assert store.load_review(key).by["by"] == "agent"


def test_a_service_brings_its_own_reviewer(tmp_path, dataset, monkeypatch):  # noqa: F811
    """nolli serve runs the reviewer it is given, and shows its reason not to run as it is."""
    class Mine(reviewer.Reviewer):
        def __init__(self):
            self.why: str | None = "No reviews left for this installation."

        def available(self):
            return self.why

        def model(self):
            return "mine-1"

        def review(self, ds, repo, out, on_step=None, cancel=None):
            assert (ds / "review.md").read_text().startswith("# Architecture review") and (repo / ".git").is_dir()
            out.mkdir(parents=True, exist_ok=True)
            (out / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "Mine."}]))
            return reviewer.Run(turns=1, model="mine-1")

    mine = Mine()
    srv = hosted.make_server(dataset.parent.parent, port=0, token=hosted.Token(), reviewer=mine,  # the dataset is in <cache>/data
                             sources=LocalRepository(dataset.parent.parent / "shop"))
    monkeypatch.setattr(srv.services.site, "ci_review", lambda key, m: {"found": None, "applies": False})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st = call(srv, f"/api/review?d={dataset.name}")[1]
        assert (st["can_run"], st["why_not"], st["model"]) == (False, "No reviews left for this installation.", "mine-1")
        mine.why = None
        call(srv, f"/api/review?d={dataset.name}", "POST")
        until(lambda: call(srv, f"/api/review?d={dataset.name}")[1]["run"]["state"] in ("done", "failed"))
        st = call(srv, f"/api/review?d={dataset.name}")[1]
        assert st["run"]["state"] == "done", st["run"]["error"]
        assert st["verdicts"]["by"] == "agent" and st["verdicts"]["model"] == "mine-1"
    finally:
        srv.shutdown()
        srv.server_close()
