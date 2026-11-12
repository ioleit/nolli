"""Keeping a cache within bounds (nolli/server/retention.py), and nolli serve keeping its own."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from nolli.logic.sources import CloneCache
from nolli.server import retention
from nolli.server.retention import LeastRecentlyUsed, parse_size, scan, sweep
from nolli.server.serve import Site
from nolli.store import RepoStoreProvider

KINDS = {"model": "data/github.com/*/*/*", "clone": "repos/*/*"}  # two kinds of entries, as any cache may have

A = "data/github.com/o/r/@" + "a" * 40 + ".." + "b" * 40  # a change's model, where RepoStoreProvider keeps it
C = "data/github.com/o/r/@" + "c" * 40  # a commit's


def make(cache: Path, rel: str, size: int, used: float) -> Path:
    p = cache / rel
    if rel.endswith(".json"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    else:
        p.mkdir(parents=True)
        (p / "f").write_bytes(b"x" * size)
    os.utime(p, (used, used))
    return p


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    now = time.time()
    make(tmp_path, A, 100, now - 300)
    make(tmp_path, C, 100, now - 100)
    make(tmp_path, "data/github.com/o/r/.@ddd.123.456", 1000, now - 999)  # work under way: never an entry
    make(tmp_path, "repos/o/r", 500, now - 200)
    make(tmp_path, "repos/p/q", 50, now - 400)
    return tmp_path


def test_scan_finds_each_kind_and_skips_work_under_way(cache):
    found = {(e.kind, str(e.path.relative_to(cache)), e.size) for e in scan(cache, KINDS)}
    assert found == {("model", A, 100), ("model", C, 100),
                     ("clone", "repos/o/r", 500), ("clone", "repos/p/q", 50)}


def test_least_recently_used_deletes_the_oldest_until_the_rest_fits(cache):
    names = lambda es: [e.path.name for e in es]
    entries = scan(cache, KINDS)  # 750 bytes in all
    assert LeastRecentlyUsed(1000)(entries) == []
    assert names(LeastRecentlyUsed(700)(entries)) == ["q"]
    assert names(LeastRecentlyUsed(600)(entries)) == ["q", Path(A).name]
    assert len(LeastRecentlyUsed(0)(entries)) == 4


def test_sweep_spares_busy_entries_and_leaves_no_empty_owner(cache):
    deleted = sweep(cache, KINDS, LeastRecentlyUsed(0), busy=lambda e: e.path.name == "r")
    assert len(deleted) == 3
    assert not (cache / "repos/p").exists()  # the owner went with its only clone
    assert (cache / "repos/o/r").is_dir()
    for kind_dir in ("data", "repos"):  # the kinds' folders stay, even empty
        assert (cache / kind_dir).is_dir()
    assert (cache / "data/github.com/o/r/.@ddd.123.456").is_dir()


def test_touch_makes_an_entry_recent(cache):
    retention.touch(cache / "repos/p/q")
    assert [e.path.name for e in LeastRecentlyUsed(650)(scan(cache, KINDS))] == [Path(A).name]


def test_parse_size():
    assert parse_size("20G") == 20 << 30
    assert parse_size("500m") == 500 << 20
    assert parse_size("1.5TiB") == int(1.5 * (1 << 40))
    assert parse_size("4096") == 4096
    with pytest.raises(ValueError):
        parse_size("lots")


def test_site_deletes_old_clones_but_not_the_store(cache):
    """The clones used longest ago go, except one held open by a build; the store is the
    provider's to keep (``cleanup``), and what nolli before 0.7 kept (extractions) is deleted
    when the site starts."""
    make(cache, "extractions/" + "a" * 40 + ".json", 10, time.time())
    site = Site(RepoStoreProvider(cache / "data"), CloneCache(cache / "repos"), cache, keep=0)
    assert not (cache / "extractions").exists()
    site.sources.held[cache / "repos/o/r"] = 1  # type: ignore[attr-defined]  # a build's clone, open
    assert {str(e.path.relative_to(cache)) for e in site.tidy()} == {"repos/p/q"}
    assert (cache / A).is_dir() and (cache / C).is_dir() and (cache / "repos/o/r").is_dir()


def test_site_without_keep_deletes_nothing(cache):
    assert Site(RepoStoreProvider(cache / "data"), CloneCache(cache / "repos"), cache).tidy() == []
    assert len(scan(cache, KINDS)) == 4
