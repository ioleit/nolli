"""nolli serve (nolli/viewer/hosted.py) without the network: which paths are pages, the viewer's
page pinned to a dataset, a build's progress as a page shows it, and a build's dataset
appearing whole or not at all, builds in processes of their own and their limits. Asking
GitHub is not tested here: it needs the network."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from nolli.progress import SHORT, Progress
from nolli.viewer.hosted import ROUTE, Board, Plan, Site, TooLarge, viewer_page


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


# Builds run in processes of their own, so they are module-level functions.

def broken(clone: Path, out: Path, progress: Progress) -> None:
    out.mkdir(parents=True)
    (out / "report.json").write_text("{}")
    raise RuntimeError("half way")


def working(clone: Path, out: Path, progress: Progress) -> None:
    with progress.step("drawing"):
        time.sleep(SHORT)  # listed as a finished step only if it took that long
        out.mkdir(parents=True)
        (out / "report.json").write_text("{}")
        (out / "pid").write_text(str(os.getpid()))
        (out / "tmp").write_text(tempfile.gettempdir())


def slow(clone: Path, out: Path, progress: Progress) -> None:
    with progress.step("thinking"):
        time.sleep(60)


def dies(clone: Path, out: Path, progress: Progress) -> None:
    os._exit(9)  # as if killed (out of memory)


def run(site: Site, plan: Plan, seconds: float = 20):
    job = site.start(plan)
    deadline = time.time() + seconds
    while job.state in ("waiting", "building") and time.time() < deadline:
        time.sleep(0.05)
    return job


@pytest.fixture
def site(tmp_path: Path) -> Site:
    subprocess.run(["git", "init", "-q", str(tmp_path / "src")], check=True)
    return Site(tmp_path / "cache")


def test_a_dataset_appears_whole_or_not_at_all(site, tmp_path):
    job = run(site, Plan("o_r_broken", "o/r", str(tmp_path / "src"), broken))
    assert job.state == "error" and "half way" in job.error
    assert not (site.data / "o_r_broken").exists()
    assert not any(p.name.endswith(".building") for p in site.data.iterdir())


def test_a_build_runs_in_its_own_process_and_reports_its_progress(site, tmp_path):
    job = run(site, Plan("o_r_ok", "o/r", str(tmp_path / "src"), working))
    assert job.state == "done", job.error
    out = site.data / "o_r_ok"
    assert int((out / "pid").read_text()) != os.getpid()
    assert Path((out / "tmp").read_text()) == site.cache / ".tmp" / "o_r_ok"  # its temporary files, deleted afterwards
    assert not (site.cache / ".tmp" / "o_r_ok").exists()
    assert any(line.startswith("drawing") for line in job.board.lines)


def test_a_build_past_the_timeout_is_stopped(site, tmp_path):
    site.timeout = 1
    t0 = time.time()
    job = run(site, Plan("o_r_slow", "o/r", str(tmp_path / "src"), slow))
    assert job.state == "error" and "stopped after 1 s" in job.error
    assert time.time() - t0 < 15
    assert job.board.current.startswith("thinking")  # what it was doing when it was stopped


def test_a_build_whose_process_dies_is_an_error(site, tmp_path):
    job = run(site, Plan("o_r_dies", "o/r", str(tmp_path / "src"), dies))
    assert job.state == "error" and "exit code 9" in job.error


def test_a_repository_too_large_is_not_built(site, monkeypatch):
    site.max_repo = 500 << 20
    monkeypatch.setattr(site, "api", lambda path: {"full_name": "o/big", "size": 600 << 10, "default_branch": "main"})
    with pytest.raises(TooLarge, match="600 MB"):
        site.plan("o", "big")
