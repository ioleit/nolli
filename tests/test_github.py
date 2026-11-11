"""Which targets name something on GitHub, and which remotes are on GitHub
(nolli/logic/sources/github.py). The API and the fetch are not tested here: they need the network."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.logic.sources.git import ChangeError
from nolli.logic.sources.github import Target, _remotes, parse_target


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_urls():
    assert parse_target("https://github.com/o/r") == Target("o", "r", "repo")
    assert parse_target("https://github.com/o/r.git/") == Target("o", "r", "repo")
    assert parse_target("https://github.com/o/r/tree/feat/x") == Target("o", "r", "repo", ref="feat/x")
    assert parse_target("https://github.com/o/r/commit/abc1234") == Target("o", "r", "commit", ref="abc1234")
    assert parse_target("https://github.com/o/r/commit/abc1234/files?w=1") == Target("o", "r", "commit", ref="abc1234")
    for url in ("https://github.com/o/r/pull/12", "https://github.com/o/r/pull/12/files", "http://github.com/o/r.git/pulls/12"):
        assert parse_target(url) == Target("o", "r", "pull", number=12), url
    assert parse_target("https://github.com/o/r/compare/main...feat/x") == Target("o", "r", "compare", base="main", head="feat/x")
    assert parse_target("https://github.com/o/r/compare/a..b") == Target("o", "r", "compare", base="a", head="b", symmetric=False)
    for text in (None, "", "main", "12", "a..b", "https://gitlab.com/o/r/-/merge_requests/1"):
        assert parse_target(text) is None, text
    for url in ("https://github.com/o/r/issues/3", "https://github.com/o/r/compare/main", "https://github.com/o/r/pull/x"):
        with pytest.raises(ChangeError, match="nolli builds"):
            parse_target(url)


def test_numbers_are_pull_requests_of_the_remote(tmp_path: Path):
    git(tmp_path, "init", "-q")
    with pytest.raises(ChangeError, match="no GitHub remote"):
        parse_target("#12", tmp_path)
    git(tmp_path, "remote", "add", "origin", "git@github.com:me/fork.git")
    for text in ("#12", "pr:12", "PR/12"):
        assert parse_target(text, tmp_path) == Target("me", "fork", "pull", number=12), text
    git(tmp_path, "remote", "add", "upstream", "https://github.com/them/proj")
    assert parse_target("#3", tmp_path) == Target("them", "proj", "pull", number=3)  # upstream first
    with pytest.raises(ChangeError, match="no GitHub remote"):
        parse_target("#3")  # not in a repository


def test_remotes(tmp_path: Path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", "git@github.com:me/fork.git")
    git(tmp_path, "remote", "add", "upstream", "https://github.com/them/proj")
    git(tmp_path, "remote", "add", "work", "git@git.example.com:proj.git")
    assert _remotes(tmp_path) == {"origin": ("me", "fork"), "upstream": ("them", "proj")}
