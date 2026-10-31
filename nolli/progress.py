"""What a build is doing, on stderr, while it does it.

A step that takes half a second or more ends with a line saying how long it took; quicker
ones leave none, so a small repository's build says only that it was built. On a terminal
the step under way is also one line rewritten in place, with the files it has read so far
and the time since the build began. Elsewhere (an agent, a pipe) only the finished steps'
lines are printed.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, TextIO, TypeVar

T = TypeVar("T")
SHORT = 0.5  # s: a step this quick leaves no line
EVERY = 0.1  # s: how often the line under way is rewritten


class Progress:
    def __init__(self, stream: TextIO | None = None, prefix: str = "", t0: float | None = None):
        self.stream = stream or sys.stderr
        self.live = self.stream.isatty()
        self.prefix = prefix
        self.t0 = time.time() if t0 is None else t0
        self.label = ""
        self.last = 0.0

    def under(self, prefix: str) -> "Progress":
        """The same progress, its steps labelled ``prefix`` (the side of a change: base, head)."""
        p = Progress(self.stream, f"{self.prefix}{prefix}: ", self.t0)
        p.live = self.live
        return p

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        self.label, t0 = f"{self.prefix}{label}", time.time()
        self._show(self.label, force=True)
        yield
        took = time.time() - t0
        if self.live:
            self._clear()
        if took >= SHORT:
            print(f"  {self.label:<34} {took:6.1f} s", file=self.stream, flush=True)
        self.label = ""

    def each(self, items: Iterable[T], total: int | None = None, unit: str = "files") -> Iterator[T]:
        """``items``, counting them on the line under way."""
        if not self.live or not self.label:
            yield from items
            return
        n = total if total is not None else len(items) if hasattr(items, "__len__") else None  # type: ignore[arg-type]
        for i, x in enumerate(items):
            if time.time() - self.last >= EVERY:
                self._show(f"{self.label}  {i}/{n} {unit}" if n else f"{self.label}  {i} {unit}")
            yield x

    def _show(self, text: str, force: bool = False) -> None:
        if not self.live:
            return
        now = time.time()
        if not force and now - self.last < EVERY:
            return
        self.last = now
        line = f"  {text}  … {now - self.t0:.0f} s"
        self.stream.write("\r\x1b[K" + line[: _width() - 1])
        self.stream.flush()

    def _clear(self) -> None:
        self.stream.write("\r\x1b[K")
        self.stream.flush()


class Quiet(Progress):
    """No progress at all: the default where nobody asked for any."""

    def __init__(self):
        super().__init__(stream=sys.stderr)
        self.live = False

    def under(self, prefix: str) -> "Progress":
        return self

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        yield

    def each(self, items, total=None, unit="files"):
        return iter(items)


def _width() -> int:
    import shutil

    return shutil.get_terminal_size((100, 24)).columns
