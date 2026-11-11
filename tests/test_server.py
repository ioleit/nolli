"""The viewer's server (nolli/server/serve.py) without the network: the pages of local
targets and their builds, the viewer's page and API by model key, a build's progress as a page
shows it, a build's steps in processes of their own and their limits, and a pull request built
from a local repository standing in for its clone. Asking GitHub is faked: it needs the network."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

from nolli.logic.sources import LocalRepository
from nolli.logic.sources.git import ChangeError
from nolli.model.keys import Repo
from nolli.progress import SHORT, Progress
from nolli.server.serve import Board, Plan, Site, TooLarge, make_local_server, make_server, viewer_page
from nolli.store import DirectoryStore


def test_viewer_page_names_its_model():
    key = Repo('github.com/o/"r').make_commit("a" * 40)
    page = viewer_page(key, "o/r#1: <b>").decode()
    assert '<base href="/">' in page
    assert f'<meta name="nolli-key" content="github.com/o/&quot;r@{"a" * 40}">' in page
    assert "<title>o/r#1: &lt;b&gt; · nolli</title>" in page


def test_board_keeps_finished_steps_and_the_one_under_way():
    board = Board()
    p = Progress(board).under("head")  # type: ignore[arg-type]
    with p.step("python imports"):
        for _ in p.each(range(3)):
            pass
        assert board.current.startswith("head: python imports")
    assert board.current == ""
    board.write("  head: python symbols    1.4 s\n")
    assert board.lines == ["head: python symbols    1.4 s"]


# A build's steps run in processes of their own, so they are module-level functions.

def working(n: int, progress: Progress) -> dict:
    with progress.step("drawing"):
        time.sleep(SHORT)  # listed as a finished step only if it took that long
    return {"n": n, "pid": os.getpid(), "tmp": tempfile.gettempdir()}


def failing(progress: Progress) -> None:
    raise RuntimeError("half way")


def slow(progress: Progress) -> None:
    with progress.step("thinking"):
        time.sleep(60)


def dies(progress: Progress) -> None:
    os._exit(9)  # as if killed (out of memory)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def src(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A repository with a main branch and a branch off it: (path, main's tip, the branch's base, its head)."""
    r = tmp_path / "src"
    (r / "shop").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / "shop/__init__.py").write_text("")
    (r / "shop/store.py").write_text("class Store:\n    pass\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "shop")
    base = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "-b", "cache")
    (r / "shop/cache.py").write_text("from .store import Store\n\nclass Cache:\n    s = Store()\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "a cache")
    head = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "main")
    (r / "README.md").write_text("later on main\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "main moves on")
    return r, git(r, "rev-parse", "HEAD"), base, head


@pytest.fixture
def site(tmp_path: Path, src) -> Site:
    return Site(DirectoryStore(tmp_path / "cache" / "data"), LocalRepository(src[0]), tmp_path / "cache")


def run(site: Site, plan: Plan, seconds: float = 60):
    job = site.start(plan)
    deadline = time.time() + seconds
    while job.state in ("waiting", "building") and time.time() < deadline:
        time.sleep(0.05)
    return job


def test_a_step_runs_in_its_own_process_and_reports_its_progress(site):
    board = Board()
    out = site.run_step(working, 7, progress=Progress(board).under("head"))  # type: ignore[arg-type]
    assert out["n"] == 7 and out["pid"] != os.getpid()
    assert Path(out["tmp"]).parent == site.work / ".tmp" and not Path(out["tmp"]).exists()  # its temporary files, deleted afterwards
    assert any(line.startswith("head: drawing") for line in board.lines)


def test_a_failing_step_is_an_error(site):
    with pytest.raises(ChangeError, match="RuntimeError: half way"):
        site.run_step(failing, progress=Progress(Board()))  # type: ignore[arg-type]


def test_a_step_past_the_timeout_is_stopped(site):
    site.timeout = 1
    board = Board()
    t0 = time.time()
    with pytest.raises(ChangeError, match="stopped after 1 s"):
        site.run_step(slow, progress=Progress(board))  # type: ignore[arg-type]
    assert time.time() - t0 < 15
    assert board.current.startswith("thinking")  # what it was doing when it was stopped


def test_a_step_whose_process_dies_is_an_error(site):
    with pytest.raises(ChangeError, match="exit code 9"):
        site.run_step(dies, progress=Progress(Board()))  # type: ignore[arg-type]


def fake_github(answers: dict):
    """GitHub's API answering ``answers`` (by path), and nothing else."""
    def get(path: str) -> dict:
        if path not in answers:
            raise AssertionError(f"asked GitHub for {path}")
        return answers[path]
    return get


def test_a_pull_request_is_built_from_where_it_left_its_base(site, src, monkeypatch):
    """The plan has GitHub's answer; the build needs the commits only, not GitHub."""
    _r, main_tip, base, head = src
    monkeypatch.setattr(site, "get", fake_github({
        "repos/o/r": {"full_name": "o/r", "size": 1},
        "repos/o/r/pulls/7": {"head": {"sha": head}, "base": {"sha": main_tip}, "title": "a cache", "body": "the description"}}))
    plan = site.plan("https://github.com/o/r/pull/7")
    assert plan.key is None and plan.title == "o/r #7: a cache"  # where it left its base needs the commits
    job = run(site, plan)
    assert job.state == "done", job.error
    assert job.key == Repo("github.com/o/r").make_change(base, head)  # the merge base, not main's tip
    key, sc = site.find_built(plan)
    about = sc.model.meta["change"]
    assert key == job.key and about["pr"] == 7 and about["description"] == "the description"
    assert about["remote"] == "https://github.com/o/r" and sc.find_element("repo:shop/cache.py").get("change")["status"] == "added"
    assert site.store.load_model(Repo("github.com/o/r").make_commit(base)) is not None  # its base, kept for the next push


def test_a_build_that_fails_leaves_no_model(site, src, monkeypatch):
    from nolli.server import serve

    _r, _tip, base, head = src
    monkeypatch.setattr(serve.build, "fetch_change", lambda *a, **k: (_ for _ in ()).throw(ChangeError("half way")))
    job = run(site, Plan("o_r_broken", "o/r", key=Repo("github.com/o/r").make_change(base, head)))
    assert job.state == "error" and job.error == "half way"
    assert site.find_built(job.plan) is None


def test_a_repository_too_large_is_not_built(site, monkeypatch):
    site.max_repo = 500 << 20
    monkeypatch.setattr(site, "get", fake_github({"repos/o/big": {"full_name": "o/big", "private": False, "size": 600 << 10}}))
    with pytest.raises(TooLarge, match="600 MB"):
        site.plan("https://github.com/o/big")


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


@pytest.fixture
def served(tmp_path: Path, src):
    """nolli view's server on a data directory and the repository ``src``."""
    srv = make_local_server(tmp_path / "data", src[0], port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    srv.shutdown()
    srv.server_close()


def test_a_targets_page_builds_it_then_shows_it(served, src):
    url, srv = served
    _r, _tip, base, head = src
    status, body = get(f"{url}/?t={quote('...cache')}")
    assert status == 200 and 'id="log"' in body  # not built yet: the page follows its build
    key = Repo(str(src[0].resolve())).make_change(base, head)
    deadline = time.time() + 60
    while json.loads(get(f"{url}/api/job?j={quote(key.format_key())}")[1])["state"] not in ("done", "error") and time.time() < deadline:
        time.sleep(0.1)
    status, body = get(f"{url}/?t={quote('...cache')}")
    assert status == 200 and f'<meta name="nolli-key" content="{key.format_key()}">' in body
    status, body = get(f"{url}/api/scene?k={quote(key.format_key())}&p=changes")
    assert status == 200 and json.loads(body)["dataset"]["key"] == key.format_key()
    assert get(f"{url}/api/scene?k={quote(key.format_key()[:-1])}")[0] == 404  # no such model
    assert f'content="{key.format_key()}"' in get(f"{url}/?t={quote(key.format_key())}")[1]  # a key: built, shown as it is
    assert get(f"{url}/?t={quote(str(srv.services.store.locate(key)))}")[0] == 404  # a dataset directory is no target (nolli view's own)
    assert get(f"{url}/api/list")[0] == 404  # the store is not listed


def test_pages_that_show_nothing(served, tmp_path):
    url, _ = served
    status, body = get(f"{url}/?t=nope")
    assert status == 404 and "no commit" in body
    assert get(f"{url}/nope")[0] == 404
    assert "<form" in get(f"{url}/")[1]  # the landing page


def test_a_server_without_a_repository_shows_githubs_paths_only(tmp_path, src):
    store = DirectoryStore(tmp_path / "data")
    srv = make_server(store, LocalRepository(src[0]), tmp_path, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, body = get(f"http://127.0.0.1:{srv.server_address[1]}/?t=main")
        assert status == 404 and "this host shows GitHub&#x27;s paths" in body
    finally:
        srv.shutdown()
        srv.server_close()
