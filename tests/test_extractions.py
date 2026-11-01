"""Extractions kept by commit (nolli/extractions.py): a dataset built from kept sides is the
dataset built from scratch, and a kept side is not extracted again."""

from __future__ import annotations

import json
from pathlib import Path

from test_diff import repo  # noqa: F401  # the fixture: a repository with a base and a head commit

import nolli.build
from nolli.build import build_commit, build_diff
from nolli.extractions import Extractions


def dataset(out: Path) -> dict:
    """A dataset's files, without what says when or how fast it was built."""
    files = {}
    for f in sorted(out.iterdir()):
        text = f.read_text()
        if f.suffix == ".json":
            d = json.loads(text)
            meta = d.get("meta", {}) if isinstance(d, dict) else {}  # changes.json is a list
            for m in (meta, meta.get("diff") or {}):
                for k in ("generated_at", "timings", "seconds"):
                    m.pop(k, None)
            text = json.dumps(d, sort_keys=True)
        files[f.name] = text
    return files


def counting(monkeypatch) -> list[Path]:
    calls: list[Path] = []
    real = nolli.build.extract

    def extract(path, *args, **kwargs):
        calls.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(nolli.build, "extract", extract)
    return calls


def test_a_change_from_kept_sides_is_the_change_from_scratch(repo, tmp_path, monkeypatch):  # noqa: F811
    path, base, head = repo
    calls = counting(monkeypatch)
    store = Extractions(tmp_path / "extractions", "1.0")
    build_diff(path, base, head, tmp_path / "scratch", 34, pr=1, title="t")
    assert len(calls) == 2
    build_diff(path, base, head, tmp_path / "first", 34, pr=1, title="t", extractions=store)
    assert len(calls) == 4
    assert sorted(f.name for f in store.root.iterdir()) == sorted([f"{base}.json", f"{head}.json"])
    build_diff(path, base, head, tmp_path / "again", 34, pr=1, title="t", extractions=store)
    assert len(calls) == 4  # both sides kept
    assert dataset(tmp_path / "again") == dataset(tmp_path / "first") == dataset(tmp_path / "scratch")


def test_a_commit_shares_its_extraction_with_a_change_on_top(repo, tmp_path, monkeypatch):  # noqa: F811
    path, base, head = repo
    calls = counting(monkeypatch)
    store = Extractions(tmp_path / "extractions", "1.0")
    build_commit(path, base, tmp_path / "scratch", 34)
    build_commit(path, base, tmp_path / "kept", 34, extractions=store)
    assert len(calls) == 2
    build_commit(path, base, tmp_path / "again", 34, extractions=store)
    build_diff(path, base, head, tmp_path / "change", 34, extractions=store)
    assert len(calls) == 3  # the base came from the store both times; only the head was extracted
    assert dataset(tmp_path / "again") == dataset(tmp_path / "kept") == dataset(tmp_path / "scratch")


def test_another_version_or_a_broken_file_is_a_miss(repo, tmp_path):  # noqa: F811
    path, base, _head = repo
    old = Extractions(tmp_path / "x", "1.0")
    build_commit(path, base, tmp_path / "out", 34, extractions=old)
    assert old.get(base) is not None
    assert Extractions(tmp_path / "x", "1.1").get(base) is None
    old.path(base).write_text("{not json")
    assert old.get(base) is None
    assert old.get("0" * 40) is None
