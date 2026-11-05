"""nolli serve (nolli/server/hosted.py) without the network: which paths are pages, the viewer's
page pinned to a model, a build's progress as a page shows it, a build's steps in processes of
their own and their limits, and a pull request built from a local repository standing in for
its clone. Asking GitHub is not tested here: it needs the network."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from nolli.model.keys import Repo
from nolli.progress import SHORT, Progress
from nolli.sources import LocalRepository
from nolli.sources.spec import ChangeError
from nolli.server.hosted import ROUTE, Board, Plan, Site, TooLarge, viewer_page


def test_routes():
    def parts(path: str):
        m = ROUTE.match(path)
        return m and (m["owner"], m["repo"], m["n"], m["sha"])

    assert parts("/ioleit/nolli") == ("ioleit", "nolli", None, None)
    assert parts("/ioleit/nolli/") == ("ioleit", "nolli", None, None)
    assert parts("/o/r.js/pull/23") == ("o", "r.js", "23", None)
    assert parts("/o/r/pull/23/files") == ("o", "r", "23", None)
    assert parts("/o/r/pulls/23") == ("o", "r", "23", None)
    assert parts("/o/r/commit/a34bf10") == ("o", "r", None, "a34bf10")
    for path in ("/", "/o", "/o/r/pull/x", "/o/r/commit/xyz1234", "/o/r/tree/main", "/-o/r", "/o/r/pull/1/2/3", "/o/../r"):
        assert not parts(path), path


def test_viewer_page_pins_the_dataset():
    page = viewer_page('o_r_pr1_"x', "o/r#1: <b>").decode()
    assert '<base href="/">' in page
    assert '<meta name="nolli-dataset" content="o_r_pr1_&quot;x">' in page
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
    return Site(tmp_path / "cache", sources=LocalRepository(src[0]))


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
    assert Path(out["tmp"]).parent == site.cache / ".tmp" and not Path(out["tmp"]).exists()  # its temporary files, deleted afterwards
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


def test_a_pull_request_is_built_from_where_it_left_its_base(site, src, monkeypatch):
    """The plan has GitHub's answer; the build needs the commits only, not GitHub."""
    from nolli.sources import github

    def no_api(*args, **kwargs):
        raise AssertionError("the build asked GitHub's API")

    monkeypatch.setattr(github, "api", no_api)
    _r, main_tip, base, head = src
    plan = Plan("o_r_pr7", "o/r#7: a cache", Repo("github.com/o/r"), pull=(main_tip, head),
                about={"pr": 7, "title": "a cache", "description": "the description"})
    job = run(site, plan)
    assert job.state == "done", job.error
    assert job.key == Repo("github.com/o/r").make_change(base, head)  # the merge base, not main's tip
    key, sc = site.find_built(plan)
    about = sc.model.meta["change"]
    assert key == job.key and about["pr"] == 7 and about["description"] == "the description"
    assert about["remote"] == "https://github.com/o/r" and sc.find_element("repo:shop/cache.py").get("change")["status"] == "added"
    assert site.store.load_model(Repo("github.com/o/r").make_commit(base)) is not None  # its base, kept for the next push


def test_a_build_that_fails_leaves_no_model(site, src, monkeypatch):
    from nolli.server import hosted

    _r, _tip, base, head = src
    monkeypatch.setattr(hosted.Datasets, "build", lambda *a, **k: (_ for _ in ()).throw(ChangeError("half way")))
    job = run(site, Plan("o_r_broken", "o/r", Repo("github.com/o/r"), key=Repo("github.com/o/r").make_change(base, head)))
    assert job.state == "error" and job.error == "half way"
    assert site.find_built(job.plan) is None


def test_a_repository_too_large_is_not_built(site, monkeypatch):
    site.max_repo = 500 << 20
    monkeypatch.setattr(site, "api", lambda owner, repo, rest="": {"full_name": "o/big", "private": False, "size": 600 << 10, "default_branch": "main"})
    with pytest.raises(TooLarge, match="600 MB"):
        site.plan("o", "big")
