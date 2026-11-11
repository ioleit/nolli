"""What a target names (nolli/operations.py: Target.from_string, Target.pin; nolli/logic/sources/git.py),
on a small git history:

    main:    c1 - c2 ------- m (merge of feature, --no-ff)
                    \\       /
    feature:         f1 - f2

and what nolli agent brief makes of the forms the review skill (nolli/skill/SKILL.md) passes:
an installed skill is never updated, so each must keep its meaning."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from nolli.cli import main
from nolli.operations import Target
from nolli.logic.sources.git import WORKTREE, ChangeError, Revs, _default_branch, find_toplevel
from nolli.model.keys import Repo
from nolli.store import DirectoryStore


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


def pin(r: Path, text: str | None, review: bool = False) -> Target:
    """``text`` pinned in the repository around ``r``, as the command line does."""
    try:
        local = find_toplevel(r)
    except ChangeError:
        local = None
    return Target.from_string(text, local).pin(review=review)


def ends(r: Path, text: str | None, review: bool = False) -> tuple[str | None, str]:
    key = pin(r, text, review).key
    return (key.base, key.head) if hasattr(key, "base") else (None, key.sha)


def unmerge(r: Path, c: dict[str, str]) -> None:
    """main back to c2, feature checked out: feature is not merged yet."""
    git(r, "reset", "-q", "--hard", c["c2"])
    git(r, "checkout", "-q", "feature")


def test_forms():
    def revs(text):
        t = Target.from_string(text)
        assert t.kind == "revs"
        return t.revs

    assert revs(None) == Revs("range", head="HEAD", symmetric=True)
    assert revs("main") == Revs("snapshot", head="main")
    assert revs(WORKTREE) == Revs("snapshot", head=WORKTREE)
    assert revs("a..b") == Revs("range", base="a", head="b")
    assert revs("a...b") == Revs("range", base="a", head="b", symmetric=True)
    assert revs("...b") == Revs("range", head="b", symmetric=True)
    assert revs("HEAD..WORKTREE") == Revs("range", base="HEAD", head=WORKTREE)
    assert revs("v1^!") == Revs("own", head="v1")
    assert Target.from_string("https://github.com/o/r").kind == "github"
    key = Repo("github.com/o/r").make_change("a" * 40, "b" * 40)
    assert Target.from_string(key.format_key()) == Target("key", key=key)
    assert revs("o/r@abc..def").kind == "range" and revs("main@{u}").kind == "snapshot"  # no key: short shas, a reflog


def test_default_branch(repo):
    r, _ = repo
    assert _default_branch(r) == "main"


def test_nothing_is_the_branchs_committed_change(repo):
    r, c = repo
    unmerge(r, c)
    target = pin(r, None)
    assert (target.key.base, target.key.head) == (c["c2"], c["f2"])
    assert target.about["title"] == "feature"
    assert target.about["description"] == f"2 commits:\n\n- Add f1 ({c['f1'][:7]})\n- Add f2 ({c['f2'][:7]})"


def test_nothing_on_the_default_branch_is_an_error(repo):
    r, _ = repo
    with pytest.raises(ChangeError, match="nothing to build by default.*WORKTREE"):
        ends(r, None)


def test_a_revision_is_a_snapshot(repo):
    r, c = repo
    assert ends(r, "main") == (None, c["m"])
    assert ends(r, c["f1"][:7]) == (None, c["f1"])
    assert pin(r, "main").key == pin(r, "main").key.repo.make_commit(c["m"])


def test_what_a_branch_adds(repo):
    r, c = repo
    with pytest.raises(ChangeError, match=r"feature\^! is its own change"):
        ends(r, "...feature")  # merged: nothing between main and feature
    unmerge(r, c)
    assert ends(r, "...feature") == (c["c2"], c["f2"])


def test_own_change(repo):
    r, c = repo
    assert ends(r, f"{c['m'][:7]}^!") == (c["c2"], c["m"])  # the merge commit: against its first parent
    target = pin(r, f"{c['f1']}^!")
    assert (target.key.base, target.key.head, target.about["title"], target.about["description"]) == (c["c2"], c["f1"], "Add f1", "Why f1 exists.")
    with pytest.raises(ChangeError, match="no parent"):
        ends(r, f"{c['c1']}^!")


def test_ranges(repo):
    r, c = repo
    assert ends(r, f"{c['c1']}..{c['f2']}") == (c["c1"], c["f2"])
    assert ends(r, f"{c['f2']}...main") == (c["f2"], c["m"])  # from where they diverged (f2, merged) to main
    with pytest.raises(ChangeError, match="is no change"):
        ends(r, "main..main")


def test_the_skills_forms_keep_their_meaning_in_a_review(repo):
    """SKILL.md §1, as nolli agent brief has read them since 0.2: nothing, a branch or commit
    (what it adds to the default branch), <base>..<head>, <base>...<head>."""
    r, c = repo
    assert ends(r, "main", review=True) == (c["c2"], c["m"])  # on the default branch: its own change
    assert ends(r, c["f1"], review=True) == (c["c2"], c["f1"])  # merged already: against its parent
    assert ends(r, f"{c['c1']}..{c['f2']}", review=True) == (c["c1"], c["f2"])
    assert ends(r, f"{c['f2']}...main", review=True) == (c["f2"], c["m"])
    with pytest.raises(ChangeError, match="no change to review.*name one: a branch"):
        ends(r, None, review=True)  # on the default branch: as before, an error
    unmerge(r, c)
    assert ends(r, None, review=True) == (c["c2"], c["f2"])
    assert ends(r, "feature", review=True) == (c["c2"], c["f2"])  # what it adds to the default branch
    with pytest.raises(ChangeError, match="a review needs a change"):
        ends(r, c["c1"], review=True)  # a root commit is a snapshot
    with pytest.raises(ChangeError, match="a review needs a change"):
        ends(r, WORKTREE, review=True)


def test_worktree(repo):
    r, c = repo
    assert ends(r, WORKTREE) == (None, c["m"])  # no edits: HEAD itself
    refs = git(r, "for-each-ref")
    (r / "c1.py").write_text("X = 'edited'\n")
    (r / "new.py").write_text("Y = 1\n")
    (r / ".gitignore").write_text("ignored.py\n")
    (r / "ignored.py").write_text("Z = 1\n")
    status = git(r, "status", "--porcelain")
    _, wt = ends(r, WORKTREE)
    assert wt != c["m"] and ends(r, WORKTREE) == (None, wt)  # the same files: the same commit, so a build is reused
    assert git(r, "rev-parse", f"{wt}^") == c["m"]
    files = git(r, "ls-tree", "--name-only", wt).split()
    assert {"new.py", ".gitignore", "c1.py"} <= set(files) and "ignored.py" not in files
    assert git(r, "show", f"{wt}:c1.py") == "X = 'edited'"
    assert git(r, "status", "--porcelain") == status and git(r, "for-each-ref") == refs  # the index and refs untouched
    assert ends(r, f"HEAD..{WORKTREE}") == (c["m"], wt)
    assert pin(r, f"HEAD..{WORKTREE}").about["title"] == "Uncommitted edits"
    (r / "new.py").write_text("Y = 2\n")
    assert ends(r, WORKTREE)[1] != wt


def test_a_branch_called_worktree_is_refused(repo):
    r, _ = repo
    git(r, "branch", WORKTREE)
    with pytest.raises(ChangeError, match="rename it"):
        ends(r, WORKTREE)


def test_errors(repo, tmp_path):
    r, _ = repo
    with pytest.raises(ChangeError, match="no commit"):
        ends(r, "nope")
    with pytest.raises(ChangeError, match="no commit"):
        ends(r, "nope..main")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ChangeError, match="not in a git repository"):
        ends(elsewhere, "main")
    git(r, "branch", "-q", "-m", "main", "trunk")  # no main/master/origin
    with pytest.raises(ChangeError, match="name both ends"):
        ends(r, "...feature")
    with pytest.raises(ChangeError, match="name both ends"):
        ends(r, "feature", review=True)


def test_build_and_the_older_shorthands(repo, tmp_path, capsys):
    r, c = repo
    data = tmp_path / "data"
    assert main(["build", "main", "--repo", str(r), "--data", str(data)]) == 0
    built = Path(capsys.readouterr().out.strip())
    assert built.parent == data and json.loads((built / "key.json").read_text())["head"] == c["m"]
    store, key = DirectoryStore.find_dataset(built)
    assert store.root == data.resolve() and key.sha == c["m"]
    assert Target.from_string(key.format_key()).pin().key == key  # a key: stable already
    assert main(["build", str(built), "--data", str(tmp_path / "other")]) == 0  # a dataset directory: nothing to build
    assert Path(capsys.readouterr().out.strip()) == built.resolve()

    (r / "new.py").write_text("Y = 1\n")
    assert main(["extract", str(r), "-o", str(tmp_path / "x")]) == 0  # nolli build WORKTREE -o
    capsys.readouterr()
    head = json.loads((tmp_path / "x" / "key.json").read_text())["head"]
    assert head == ends(r, WORKTREE)[1] and (tmp_path / "x" / "report.json").is_file()

    assert main(["diff", str(r), c["c2"], c["f2"], "-o", str(tmp_path / "d"), "--title", "two"]) == 0  # build <base>..<head> -o
    assert json.loads(capsys.readouterr().out)["diff"]["base"] == c["c2"][:7]
    assert (tmp_path / "d" / "review.md").is_file()

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("A = 1\n")
    assert main(["extract", str(plain), "-o", str(tmp_path / "p")]) == 0  # a directory that is no git repository
    assert (tmp_path / "p" / "key.json").is_file()


def test_whole_is_gone(repo, capsys):
    r, _ = repo
    with pytest.raises(SystemExit):
        main(["view", "--whole", "--repo", str(r)])
    assert "--whole is gone: name a snapshot" in capsys.readouterr().err
