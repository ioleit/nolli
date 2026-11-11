"""Reviews in the viewer's server (nolli/server/reviews.py, /api/review): where the verdicts came from,
whether a review agent can run, a run's states through start, cancel and the end, uploads
where they are stored and refused where they are not. The agent itself (nolli/review/agent.py) is replaced: it needs the API."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

from nolli.build import fetch_change
from nolli.logic.sources import CloneCache, LocalRepository
from nolli.model.keys import Repo
from nolli.review import agent as reviewer
from nolli.review.verdicts import _keep_verdicts_file, parse_verdicts
from nolli.scenes import _open_change_scene
from nolli.server import serve
from nolli.store import DirectoryStore

GOOD = json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}])


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """A change's dataset as built for PR #7 of me/shop, in <tmp>/data; the
    repository it came from in <tmp>/shop."""
    r = tmp_path / "shop"
    (r / "shop").mkdir(parents=True)
    (r / "shop/__init__.py").write_text("")
    (r / "shop/store.py").write_text("class Store:\n    pass\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "shop")
    base = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "-b", "cache")
    (r / "shop/cache.py").write_text("from .store import Store\n\nclass Cache:\n    s = Store()\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "Add a cache")
    key = Repo("github.com/me/shop").make_change(base, git(r, "rev-parse", "HEAD"))  # as if built from '#7' on GitHub
    store = DirectoryStore(tmp_path / "data")
    fetch_change(store, LocalRepository(r), key, about={"pr": 7, "title": "Add a cache"})
    return store.locate(key)


def open_dataset(ds: Path):
    """The dataset's store, key and scene."""
    store = DirectoryStore(ds.parent)
    key = store.find_key(ds.name)
    return store, key, _open_change_scene(store, key)


def k(ds: Path) -> str:
    """The dataset's key, as the viewer's API takes it."""
    return quote(open_dataset(ds)[1].format_key(), safe="")


def kept_verdicts(ds: Path) -> list[dict]:
    store, key, _ = open_dataset(ds)
    return parse_verdicts(store.open_maps("verdicts").load(key.format_key()))


def call(srv, path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method), timeout=10) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


@pytest.fixture
def server(dataset):
    srv = serve.make_local_server(dataset.parent, dataset.parent.parent / "shop", port=0)  # nolli view's
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def until(cond, seconds: float = 10) -> None:
    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        time.sleep(0.05)
    assert cond()


def test_without_a_key(server, dataset, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, st = call(server, f"/api/review?k={k(dataset)}")
    assert status == 200 and st["verdicts"] is None and st["run"] is None
    assert (st["can_run"], st["why_not"], st["uploads"], st["server"]) == (False, "key", True, "view")
    assert call(server, f"/api/review?k={k(dataset)}", "POST")[0] == 409
    assert call(server, "/api/review?k=nope")[0] == 404


def test_upload_says_where_the_verdicts_came_from(server, dataset):
    v = [{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]
    assert call(server, f"/api/verdicts?k={k(dataset)}", "POST", json.dumps(v).encode()) == (200, {"saved": 1})
    assert call(server, f"/api/review?k={k(dataset)}")[1]["verdicts"]["by"] == "upload"


def test_a_run_its_cancel_and_its_end(server, dataset, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(reviewer, "_available", lambda: None)
    go = threading.Event()

    def fake(brief: str, head: str, check, repo: Path, out: Path, on_step, cancel: threading.Event) -> reviewer.Run:
        assert brief.startswith("# Architecture review") and git(repo, "cat-file", "-t", head) == "commit"
        run = reviewer.Run(turns=2)
        on_step("reading shop/cache.py", run)
        while not go.is_set():
            if cancel.is_set():
                raise reviewer.Cancelled()
            time.sleep(0.02)
        out.mkdir(parents=True, exist_ok=True)
        (out / "verdicts.json").write_text(json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "fine", "reason": "As intended."}]))
        return run

    monkeypatch.setattr(reviewer, "_review", fake)
    status, st = call(server, f"/api/review?k={k(dataset)}", "POST")
    assert status == 200 and st["can_run"] and st["run"]["state"] in ("queued", "running")
    until(lambda: call(server, f"/api/review?k={k(dataset)}")[1]["run"]["step"] == "reading shop/cache.py")
    assert call(server, f"/api/review?k={k(dataset)}", "POST")[1]["run"]["state"] == "running"  # one run at a time
    call(server, f"/api/review/cancel?k={k(dataset)}", "POST")
    until(lambda: call(server, f"/api/review?k={k(dataset)}")[1]["run"]["state"] == "cancelled")
    assert kept_verdicts(dataset) == []

    call(server, f"/api/review?k={k(dataset)}", "POST")
    go.set()
    until(lambda: call(server, f"/api/review?k={k(dataset)}")[1]["run"]["state"] == "done")
    st = call(server, f"/api/review?k={k(dataset)}")[1]
    assert st["verdicts"]["by"] == "agent" and st["verdicts"]["turns"] == 2
    assert [v["verdict"] for v in kept_verdicts(dataset)] == ["fine"]


def test_a_failed_run_keeps_the_verdicts_shown(server, dataset, monkeypatch):
    monkeypatch.setattr(reviewer, "_available", lambda: None)
    store, key, sc = open_dataset(dataset)
    _keep_verdicts_file(store, key, sc, GOOD, {"by": "upload"})

    def fails(*args, **kwargs):
        raise reviewer.Failed("the agent wrote no verdicts")

    monkeypatch.setattr(reviewer, "_review", fails)
    call(server, f"/api/review?k={k(dataset)}", "POST")
    until(lambda: call(server, f"/api/review?k={k(dataset)}")[1]["run"]["state"] == "failed")
    st = call(server, f"/api/review?k={k(dataset)}")[1]
    assert st["run"]["error"] == "the agent wrote no verdicts" and st["verdicts"]["by"] == "upload"
    assert [v["verdict"] for v in kept_verdicts(dataset)] == ["good"]


def test_serve_stores_no_uploads(tmp_path, dataset):
    shutil.copytree(dataset, tmp_path / "cache" / "data" / dataset.name)
    data = tmp_path / "cache" / "data"
    srv = serve.make_server(DirectoryStore(data), CloneCache(tmp_path / "cache" / "repos"), tmp_path / "cache", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        key = k(data / dataset.name)
        assert call(srv, f"/api/verdicts?k={key}", "POST", b"[]")[0] == 405
        st = call(srv, f"/api/review?k={key}")[1]
        assert st["server"] == "serve" and st["uploads"] is False and st["verdicts"] is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_service_brings_its_own_reviewer(tmp_path, dataset, monkeypatch):
    """nolli serve runs the reviewer it is given, and shows its reason not to run as it is."""
    class Mine(reviewer.Reviewer):
        def __init__(self):
            self.why: str | None = "No reviews left for this installation."

        def available(self):
            return self.why

        def model(self):
            return "mine-1"

        def review(self, brief, head, check, repo, out, on_step=None, cancel=None):
            assert brief.startswith("# Architecture review") and (repo / ".git").is_dir()
            text = json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "Mine."}])
            assert check(text)[1] == []  # nolli's check, on the diagram of the brief
            out.mkdir(parents=True, exist_ok=True)
            (out / "verdicts.json").write_text(text)
            return reviewer.Run(turns=1, model="mine-1")

    mine = Mine()
    srv = serve.make_server(DirectoryStore(dataset.parent), LocalRepository(dataset.parent.parent / "shop"), dataset.parent.parent,
                            port=0, token=serve.Token(), reviewer=mine)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st = call(srv, f"/api/review?k={k(dataset)}")[1]
        assert (st["can_run"], st["why_not"], st["model"]) == (False, "No reviews left for this installation.", "mine-1")
        mine.why = None
        call(srv, f"/api/review?k={k(dataset)}", "POST")
        until(lambda: call(srv, f"/api/review?k={k(dataset)}")[1]["run"]["state"] in ("done", "failed"))
        st = call(srv, f"/api/review?k={k(dataset)}")[1]
        assert st["run"]["state"] == "done", st["run"]["error"]
        assert st["verdicts"]["by"] == "agent" and st["verdicts"]["model"] == "mine-1"
    finally:
        srv.shutdown()
        srv.server_close()
