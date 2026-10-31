"""What nolli view shows for what the user typed (nolli/change.py), on a small git history:

    main:    c1 - c2 ------- m (merge of feature, --no-ff)
                    \\       /
    feature:         f1 - f2
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.change import ChangeError, dataset_name, default_branch, resolve, whole


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, msg: str) -> str:
    (repo / f"{name}.py").write_text(f"X = {name!r}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    c = {"c1": commit(r, "c1", "first"), "c2": commit(r, "c2", "second")}
    git(r, "checkout", "-q", "-b", "feature")
    c["f1"] = commit(r, "f1", "Add f1\n\nWhy f1 exists.")
    c["f2"] = commit(r, "f2", "Add f2")
    git(r, "checkout", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    c["m"] = git(r, "rev-parse", "HEAD")
    return r, c


def test_default_branch(repo):
    r, _ = repo
    assert default_branch(r) == "main"


def test_on_main_without_spec_is_the_whole_head(repo):
    r, c = repo
    ch = resolve(r, None)
    assert (ch.base, ch.head) == (None, c["m"])


def test_current_branch_against_main(repo):
    r, c = repo
    git(r, "checkout", "-q", "feature")
    git(r, "reset", "-q", "--hard", c["f2"])
    git(r, "checkout", "-q", "main")
    git(r, "reset", "-q", "--hard", c["c2"])  # main without the merge: feature is not merged yet
    git(r, "checkout", "-q", "feature")
    ch = resolve(r, None)
    assert (ch.base, ch.head) == (c["c2"], c["f2"])
    assert ch.title == "feature"
    assert ch.description == f"2 commits:\n\n- Add f1 ({c['f1'][:7]})\n- Add f2 ({c['f2'][:7]})"


def test_a_commit_on_main_is_its_own_change(repo):
    r, c = repo
    ch = resolve(r, c["m"][:7])  # the merge commit: against its first parent, i.e. the whole merged branch
    assert (ch.base, ch.head) == (c["c2"], c["m"])
    one = resolve(r, c["f1"])  # merged already, so against its parent; one commit gives subject and body
    assert (one.base, one.head, one.title, one.description) == (c["c2"], c["f1"], "Add f1", "Why f1 exists.")


def test_whole(repo):
    r, c = repo
    git(r, "checkout", "-q", "feature")
    assert (whole(r).base, whole(r).head) == (None, c["f2"])  # on a branch: HEAD as a whole, not the change
    assert whole(r, "main").head == c["m"]


def test_ranges(repo):
    r, c = repo
    two = resolve(r, f"{c['c1']}..{c['f2']}")
    assert (two.base, two.head) == (c["c1"], c["f2"])
    three = resolve(r, f"{c['f2']}...main")  # from where they diverged (f2, merged) to main
    assert (three.base, three.head) == (c["f2"], c["m"])


def test_root_commit_is_the_whole_commit(repo):
    r, c = repo
    ch = resolve(r, c["c1"])
    assert (ch.base, ch.head) == (None, c["c1"])
    assert dataset_name(r, ch) == f"proj-{c['c1'][:7]}"


def test_errors(repo, tmp_path):
    r, _ = repo
    with pytest.raises(ChangeError, match="no commit"):
        resolve(r, "nope")
    with pytest.raises(ChangeError, match="no commit"):
        resolve(r, "nope..main")
    git(r, "branch", "-q", "-m", "main", "trunk")  # no main/master/origin: a single revision needs a base
    with pytest.raises(ChangeError, match="name both ends"):
        resolve(r, "feature")
