"""Keeping a cache within bounds (nolli/server/retention.py), and nolli serve keeping its own."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from nolli.model.keys import Repo
from nolli.server.serve import KINDS, Job, Plan, Site
from nolli.sources import CloneCache
from nolli.store import DirectoryStore

from nolli.server import retention
from nolli.server.retention import LeastRecentlyUsed, parse_size, scan, sweep


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
    make(tmp_path, "data/o_r-aaaaaaa-bbbbbbb", 100, now - 300)
    make(tmp_path, "data/o_r-ccccccc", 100, now - 100)
    make(tmp_path, "data/.o_r-ddddddd.123.456", 1000, now - 999)  # work under way: never an entry
    make(tmp_path, "repos/o/r", 500, now - 200)
    make(tmp_path, "repos/p/q", 50, now - 400)
    return tmp_path


def test_scan_finds_each_kind_and_skips_work_under_way(cache):
    found = {(e.kind, str(e.path.relative_to(cache)), e.size) for e in scan(cache, KINDS)}
    assert found == {("model", "data/o_r-aaaaaaa-bbbbbbb", 100), ("model", "data/o_r-ccccccc", 100),
                     ("clone", "repos/o/r", 500), ("clone", "repos/p/q", 50)}


def test_least_recently_used_deletes_the_oldest_until_the_rest_fits(cache):
    names = lambda es: [e.path.name for e in es]
    entries = scan(cache, KINDS)  # 750 bytes in all
    assert LeastRecentlyUsed(1000)(entries) == []
    assert names(LeastRecentlyUsed(700)(entries)) == ["q"]
    assert names(LeastRecentlyUsed(600)(entries)) == ["q", "o_r-aaaaaaa-bbbbbbb"]
    assert len(LeastRecentlyUsed(0)(entries)) == 4


def test_sweep_spares_busy_entries_and_leaves_no_empty_owner(cache):
    deleted = sweep(cache, KINDS, LeastRecentlyUsed(0), busy=lambda e: e.path.name == "r")
    assert len(deleted) == 3
    assert not (cache / "repos/p").exists()  # the owner went with its only clone
    assert (cache / "repos/o/r").is_dir()
    for kind_dir in ("data", "repos"):  # the kinds' folders stay, even empty
        assert (cache / kind_dir).is_dir()
    assert (cache / "data/.o_r-ddddddd.123.456").is_dir()


def test_touch_makes_an_entry_recent(cache):
    retention.touch(cache / "repos/p/q")
    assert [e.path.name for e in LeastRecentlyUsed(650)(scan(cache, KINDS))] == ["o_r-aaaaaaa-bbbbbbb"]


def test_parse_size():
    assert parse_size("20G") == 20 << 30
    assert parse_size("500m") == 500 << 20
    assert parse_size("1.5TiB") == int(1.5 * (1 << 40))
    assert parse_size("4096") == 4096
    with pytest.raises(ValueError):
        parse_size("lots")


def test_site_keeps_what_a_build_uses(cache):
    """The model a build under way makes, and the clones held open, stay; what nolli before 0.7
    kept (extractions) is deleted when the site starts."""
    make(cache, "extractions/" + "a" * 40 + ".json", 10, time.time())
    site = Site(DirectoryStore(cache / "data"), CloneCache(cache / "repos"), cache, keep=0)
    assert not (cache / "extractions").exists()
    repo = Repo("github.com/o/r")
    plan = Plan("o_r_c", "o/r", key=repo.make_commit("c" * 40))
    site.jobs[plan.name] = Job(plan, state="building")
    site.sources.held[cache / "repos/o/r"] = 1  # type: ignore[attr-defined]  # a build's clone, open
    deleted = {str(e.path.relative_to(cache)) for e in site.tidy()}
    assert deleted == {"data/o_r-aaaaaaa-bbbbbbb", "repos/p/q"}
    assert (cache / "data/o_r-ccccccc").is_dir() and (cache / "repos/o/r").is_dir()


def test_site_without_keep_deletes_nothing(cache):
    assert Site(DirectoryStore(cache / "data"), CloneCache(cache / "repos"), cache).tidy() == []
    assert len(scan(cache, KINDS)) == 4
