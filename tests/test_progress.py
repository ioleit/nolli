"""Build progress (nolli/progress.py): finished steps as lines, the step under way rewritten on a terminal."""

from __future__ import annotations

import io

from nolli import progress
from nolli.progress import Progress


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_pipe_gets_only_steps_worth_a_line(monkeypatch):
    out = io.StringIO()
    p = Progress(out)
    monkeypatch.setattr(progress, "SHORT", 0.0)
    with p.under("head").step("python symbols"):
        assert list(p.each([1, 2, 3])) == [1, 2, 3]
    monkeypatch.setattr(progress, "SHORT", 60.0)
    with p.step("quick"):
        pass
    assert out.getvalue().splitlines() == [f"  {'head: python symbols':<34}    0.0 s"]


def test_terminal_counts_files_on_one_line(monkeypatch):
    out = Tty()
    p = Progress(out)
    monkeypatch.setattr(progress, "EVERY", 0.0)
    monkeypatch.setattr(progress, "SHORT", 0.0)
    with p.step("python imports"):
        list(p.each(["a.py", "b.py"]))
    text = out.getvalue()
    assert "python imports  1/2 files" in text and "\r\x1b[K" in text
    assert text.count("\n") == 1 and text.endswith(" s\n")
