"""Which arguments of nolli view name a GitHub pull request, and which remotes are on
GitHub (nolli/github.py). The API and the fetch are not tested here: they need the network."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nolli.github import is_pull_request, remotes


def test_pull_request_specs():
    for spec in ("https://github.com/o/r/pull/12", "https://github.com/o/r/pull/12/files", "http://github.com/o/r.git/pull/3",
                 "#12", "pr:12", "PR/12"):
        assert is_pull_request(spec), spec
    for spec in (None, "", "main", "12", "a..b", "https://gitlab.com/o/r/-/merge_requests/1", "https://github.com/o/r"):
        assert not is_pull_request(spec), spec


def test_remotes(tmp_path: Path):
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("remote", "add", "origin", "git@github.com:me/fork.git")
    git("remote", "add", "upstream", "https://github.com/them/proj")
    git("remote", "add", "work", "git@git.example.com:proj.git")
    assert remotes(tmp_path) == {"origin": ("me", "fork"), "upstream": ("them", "proj")}
