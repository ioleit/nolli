"""Reviews in the viewer's server (nolli/server/reviews.py, /api/review): where the verdicts came from,
whether a review agent can run, a run's states through start, cancel and the end, uploads
where they are stored and refused where they are not, and verdicts read from nolli's CI
comments by nolli serve. The agent itself (nolli/review/agent.py) is replaced: it needs the API."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import subprocess

from nolli.review import agent as reviewer
from nolli.build import fetch_change, open_change_scene
from nolli.review.ci import comment as ci_comment
from nolli.model.keys import Repo
from nolli.review.verdicts import find_provenance, keep_verdicts_file, parse_verdicts, save_verdicts
from nolli.sources import LocalRepository
from nolli.datasets import Datasets
from nolli.store import DirectoryStore
from nolli.server import hosted, serve

GOOD = json.dumps([{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}])


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """A change's dataset as a CI run builds it for PR #7 of me/shop, in <tmp>/data; the
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
    return store, key, open_change_scene(store, key)


def comment(ds: Path, verdicts: str | None = None) -> str:
    """The PR comment nolli's CI writes for the dataset, with ``verdicts`` (a verdicts.json) if
    given; the dataset itself is left as it was."""
    if verdicts is None:
        store, _key, sc = open_dataset(ds)
        return ci_comment(sc, Datasets(store).load_verdicts(sc))
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(ds, Path(tmp) / ds.name)
        store, key, sc = open_dataset(Path(tmp) / ds.name)
        keep_verdicts_file(store, key, sc, verdicts, {"by": "agent"})
        return ci_comment(sc, Datasets(store).load_verdicts(sc))


def bot_comment(text: str, login: str = "nolli-review[bot]", n: int = 1) -> dict:
    return {"user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}, "body": text,
            "html_url": f"https://github.com/me/shop/pull/7#issuecomment-{n}", "updated_at": f"2026-11-0{n}T00:00:00Z"}


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


def test_without_a_key(server, dataset, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, st = call(server, f"/api/review?d={dataset.name}")
    assert status == 200 and st["verdicts"] is None and st["run"] is None
    assert (st["can_run"], st["why_not"], st["uploads"], st["server"]) == (False, "key", True, "view")
    assert call(server, f"/api/review?d={dataset.name}", "POST")[0] == 409
    assert call(server, "/api/review?d=nope")[0] == 404


def test_upload_says_where_the_verdicts_came_from(server, dataset):
    v = [{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}]
    assert call(server, f"/api/verdicts?d={dataset.name}", "POST", json.dumps(v).encode()) == (200, {"saved": 1})
    assert call(server, f"/api/review?d={dataset.name}")[1]["verdicts"]["by"] == "upload"


def test_a_run_its_cancel_and_its_end(server, dataset, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(reviewer, "available", lambda: None)
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

    monkeypatch.setattr(reviewer, "review", fake)
    status, st = call(server, f"/api/review?d={dataset.name}", "POST")
    assert status == 200 and st["can_run"] and st["run"]["state"] in ("queued", "running")
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["step"] == "reading shop/cache.py")
    assert call(server, f"/api/review?d={dataset.name}", "POST")[1]["run"]["state"] == "running"  # one run at a time
    call(server, f"/api/review/cancel?d={dataset.name}", "POST")
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["state"] == "cancelled")
    assert kept_verdicts(dataset) == []

    call(server, f"/api/review?d={dataset.name}", "POST")
    go.set()
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["state"] == "done")
    st = call(server, f"/api/review?d={dataset.name}")[1]
    assert st["verdicts"]["by"] == "agent" and st["verdicts"]["turns"] == 2
    assert [v["verdict"] for v in kept_verdicts(dataset)] == ["fine"]


def test_a_failed_run_keeps_the_verdicts_shown(server, dataset, monkeypatch):
    monkeypatch.setattr(reviewer, "available", lambda: None)
    store, key, sc = open_dataset(dataset)
    keep_verdicts_file(store, key, sc, GOOD, {"by": "upload"})

    def fails(*args, **kwargs):
        raise reviewer.Failed("the agent wrote no verdicts")

    monkeypatch.setattr(reviewer, "review", fails)
    call(server, f"/api/review?d={dataset.name}", "POST")
    until(lambda: call(server, f"/api/review?d={dataset.name}")[1]["run"]["state"] == "failed")
    st = call(server, f"/api/review?d={dataset.name}")[1]
    assert st["run"]["error"] == "the agent wrote no verdicts" and st["verdicts"]["by"] == "upload"
    assert [v["verdict"] for v in kept_verdicts(dataset)] == ["good"]


def test_serve_reads_the_ci_review_and_stores_no_uploads(tmp_path, dataset, monkeypatch):
    text = comment(dataset, GOOD)
    shutil.copytree(dataset, tmp_path / "cache" / "data" / dataset.name)
    site = hosted.Site(tmp_path / "cache")
    key = site.store.find_key(dataset.name)
    m = open_change_scene(site.store, key)
    asked = []

    def api(path, tok):
        asked.append(path)
        return [bot_comment(text)]

    monkeypatch.setattr(hosted.github, "api", api)
    info = site.ci_review(key, m)
    assert asked == ["repos/me/shop/issues/7/comments?per_page=100"] and info["found"] == "verdicts"
    assert find_provenance(site.store, key)["by"] == "ci"
    assert parse_verdicts(site.store.open_maps("verdicts").load(key.format_key()))[0]["verdict"] == "good"
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


def test_from_ci_without_github(dataset):
    from nolli.server.reviews import ci_pull, from_ci

    store, key, m = open_dataset(dataset)
    assert ci_pull(m) == ("me", "shop", 7)
    without = comment(dataset)  # a run without an agent: no verdicts in it
    reviewed = comment(dataset, GOOD)

    assert from_ci(store, key, m, []) == {"found": None, "applies": True}
    assert from_ci(store, key, m, [bot_comment(without)])["found"] == "no-review" and find_provenance(store, key) is None
    info = from_ci(store, key, m, [bot_comment(reviewed)])
    assert info["found"] == "verdicts" and info["dropped"] == 0
    assert find_provenance(store, key)["by"] == "ci" and [v["verdict"] for v in kept_verdicts(dataset)] == ["good"]
    save_verdicts(store, key, kept_verdicts(dataset), {"by": "agent"})  # a review run here since: kept
    from_ci(store, key, m, [bot_comment(reviewed)])
    assert find_provenance(store, key)["by"] == "agent"


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
