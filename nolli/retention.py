"""Keeping a cache directory within bounds: what is in it, and which of it to delete.

A cache holds entries (a dataset, a clone, an extraction), each a file or a directory found
by a glob pattern under the cache. Its modification time says when it was last used:
whoever uses an entry touches it (``touch``). Names starting with a dot are work under way
and never entries.

A policy is any callable that takes all entries and returns those to delete; ``sweep``
deletes them, except the ones its caller says are busy. ``LeastRecentlyUsed`` is the first
policy: a size for the whole cache, reached by deleting what was used longest ago. Others
(a size per kind, a maximum age) take the same list.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    path: Path
    kind: str
    size: int  # bytes
    used: float  # when it was last used, as time.time()


Policy = Callable[[list[Entry]], list[Entry]]


def size_of(path: Path) -> int:
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:  # deleted meanwhile
                pass
    return total


def scan(cache: Path, kinds: dict[str, str]) -> list[Entry]:
    """The entries under ``cache``: for each kind, what its glob pattern finds."""
    out = []
    for kind, pattern in kinds.items():
        for p in cache.glob(pattern):
            if p.name.startswith("."):
                continue
            try:
                out.append(Entry(p, kind, size_of(p), p.stat().st_mtime))
            except OSError:
                pass
    return out


def touch(path: Path) -> None:
    """Mark an entry as used now."""
    try:
        os.utime(path)
    except OSError:
        pass


@dataclass
class LeastRecentlyUsed:
    """At most ``max_bytes`` in all: delete what was used longest ago until the rest fits."""
    max_bytes: int

    def __call__(self, entries: list[Entry]) -> list[Entry]:
        total, out = sum(e.size for e in entries), []
        for e in sorted(entries, key=lambda e: e.used):
            if total <= self.max_bytes:
                break
            out.append(e)
            total -= e.size
        return out


def sweep(cache: Path, kinds: dict[str, str], policy: Policy, busy: Callable[[Entry], bool] = lambda e: False) -> list[Entry]:
    """Delete what ``policy`` picks among the entries, except busy ones; the deleted entries."""
    return [e for e in policy(scan(cache, kinds)) if not busy(e) and delete(cache, kinds, e)]


def delete(cache: Path, kinds: dict[str, str], e: Entry) -> bool:
    """Delete an entry, and the folders its pattern matched through if that leaves them empty."""
    if e.path.is_dir() and not e.path.is_symlink():
        shutil.rmtree(e.path, ignore_errors=True)
    else:
        e.path.unlink(missing_ok=True)
    base = cache / fixed_part(kinds[e.kind])
    for parent in e.path.parents:  # a clone's owner folder, say; never the kind's own folder
        if parent == base or base not in parent.parents:
            break
        try:
            parent.rmdir()
        except OSError:
            break
    return True


def fixed_part(pattern: str) -> Path:
    """The folder a glob pattern starts in: ``repos`` for ``repos/*/*``."""
    fixed = []
    for part in Path(pattern).parts:
        if any(c in part for c in "*?["):
            break
        fixed.append(part)
    return Path(*fixed)


UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}


def parse_size(text: str) -> int:
    """``20G``, ``500M``, ``1.5t``, or bytes."""
    m = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?)i?b?\s*", text.lower())
    if not m:
        raise ValueError(f"not a size: {text!r} (say 20G, 500M or a number of bytes)")
    return int(float(m.group(1)) * UNITS[m.group(2)])


def ago(t: float) -> str:
    s = time.time() - t
    return f"{s / 86400:.1f} d" if s >= 86400 else f"{s / 3600:.1f} h" if s >= 3600 else f"{s / 60:.0f} min"
