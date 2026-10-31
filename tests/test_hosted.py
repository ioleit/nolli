"""nolli serve (nolli/viewer/hosted.py) without the network: which paths are pages, the viewer's
page pinned to a dataset, a build's progress as a page shows it, and a build's dataset
appearing whole or not at all. Asking GitHub is not tested here: it needs the network."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from nolli.progress import Progress
from nolli.viewer.hosted import ROUTE, Board, Plan, Site, viewer_page


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


def test_a_dataset_appears_whole_or_not_at_all(tmp_path: Path):
    site = Site(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path / "src")], check=True)

    def broken(clone: Path, out: Path, progress: Progress) -> None:
        out.mkdir(parents=True)
        (out / "report.json").write_text("{}")
        raise RuntimeError("half way")

    job = site.start(Plan("o_r_broken", "o/r", str(tmp_path / "src"), broken))
    deadline = time.time() + 10
    while job.state in ("waiting", "building") and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "error" and "half way" in job.error
    assert not (tmp_path / "data" / "o_r_broken").exists()
    assert not any(p.name.endswith(".building") for p in (tmp_path / "data").iterdir())
