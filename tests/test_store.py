"""Where models are kept and where commits come from (nolli/store.py), and building through them
(nolli/build.py): keys, the directory store, clones fetched from a remote, and a change's model
built once, its commits' models reused."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from nolli.build import extract_commit, fetch_change_model, run_here
from nolli.change import ChangeError
from nolli.model import Model
from nolli.store import CloneCache, DirectoryStore, LocalRepository, Repo, Review, read_key


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def src(tmp_path: Path) -> tuple[Path, list[str]]:
    """A repository with three commits in a row: (path, their shas)."""
    r = tmp_path / "src"
    (r / "shop").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    shas = []
    for i, (path, text) in enumerate([("shop/store.py", "class Store:\n    pass\n"),
                                      ("shop/cache.py", "from .store import Store\n\nclass Cache:\n    s = Store()\n"),
                                      ("shop/api.py", "from .cache import Cache\n\nC = Cache()\n")]):
        (r / path).write_text(text)
        git(r, "add", ".")
        git(r, "commit", "-q", "-m", f"step {i}")
        shas.append(git(r, "rev-parse", "HEAD"))
    return r, shas


def test_keys():
    repo = Repo.from_remote("git@github.com:IOleit/nolli.git")
    assert repo == Repo("github.com/IOleit/nolli") == Repo.from_remote("https://github.com/IOleit/nolli")
    change = repo.make_commit("a" * 40).make_change_to("b" * 40)
    assert change == repo.make_change("a" * 40, "b" * 40)
    assert change.list_sides() == (repo.make_commit("a" * 40), repo.make_commit("b" * 40))
    assert change.format_name() == "IOleit_nolli-aaaaaaa-bbbbbbb" and change.list_sides()[1].format_name() == "IOleit_nolli-bbbbbbb"
    assert repo.format_remote() == "https://github.com/IOleit/nolli"
    local = Repo("/home/me/src/shop")
    assert local.format_slug() == "shop" and local.format_remote() is None


def model(name: str) -> Model:
    m = Model()
    m.meta = {"repo": name}
    return m


def test_directory_store_keeps_models_and_reviews(tmp_path):
    store = DirectoryStore(tmp_path / "data")
    repo = Repo("github.com/o/r")
    c1, c2 = repo.make_commit("1" * 40), repo.make_commit("2" * 40)
    change = c1.make_change_to(c2.sha)
    assert store.load_model(change) is None and store.find_key(change.format_name()) is None
    store.save_model(c1, model("one"))
    store.save_model(change, model("change"))
    assert store.load_model(change).meta["repo"] == "change" and store.load_model(c1).meta["repo"] == "one"
    assert store.find_key(change.format_name()) == change and store.find_key(c1.format_name()) == c1
    assert set(store.list_names()) == {change.format_name(), c1.format_name()}
    for bad in ("", "..", "../data", ".hidden", "a/b"):
        assert store.find_key(bad) is None

    assert store.load_review(change) is None
    store.save_review(change, Review([{"target": "n_x", "verdict": "good", "reason": "ok", "extra": 1}], {"by": "upload"}))
    store.save_model(change, model("rebuilt"))  # the review stays with the model it answers
    review = store.load_review(change)
    assert review.verdicts == [{"target": "n_x", "verdict": "good", "reason": "ok"}] and review.by == {"by": "upload"}
    assert store.load_model(change).meta["repo"] == "rebuilt"
    assert not [p for p in store.root.iterdir() if p.name.startswith(".")]  # no work left over


def test_a_model_is_stored_whole_or_not_at_all(tmp_path):
    def broken(m, out):
        (out / "model.json").write_text("{}")
        raise RuntimeError("half way")

    store = DirectoryStore(tmp_path / "data", write=broken)
    key = Repo("github.com/o/r").make_commit("1" * 40)
    with pytest.raises(RuntimeError):
        store.save_model(key, model("x"))
    assert list(store.root.iterdir()) == []


def test_a_directory_written_elsewhere_is_found_by_its_name(tmp_path):
    """nolli diff -o writes a model under a name of its own; one from before keys were recorded
    gets its key from its meta."""
    d = tmp_path / "data" / "ledger-review"
    d.mkdir(parents=True)
    (d / "model.json").write_text(json.dumps({"meta": {}, "entities": [], "relations": []}))
    (d / "report.json").write_text(json.dumps({"meta": {"repo": "ledger", "diff": {"base": "a" * 40, "head": "b" * 40,
                                                                                 "remote": "https://github.com/o/ledger"}}}))
    key = read_key(d)
    assert key == Repo("github.com/o/ledger").make_change("a" * 40, "b" * 40)
    store = DirectoryStore(tmp_path / "data")
    assert store.find_key("ledger-review") == key and store.load_model(key) is not None
    assert store.locate(key) == d


def test_local_repository_has_its_commits(src):
    path, shas = src
    with LocalRepository(path).open_commits(Repo(str(path)), *shas) as repo:
        assert repo == path
    with pytest.raises(ChangeError, match="has no commit"):
        with LocalRepository(path).open_commits(Repo(str(path)), "f" * 40):
            pass


def test_clone_cache_fetches_what_is_missing_and_holds_the_clone(src, tmp_path, monkeypatch):
    path, shas = src
    monkeypatch.setattr(Repo, "format_remote", lambda self: str(path))  # a local remote stands in for GitHub
    asked = []
    clones = CloneCache(tmp_path / "repos", token=lambda owner, repo: asked.append((owner, repo)) or None)
    repo = Repo("github.com/o/shop")
    with clones.open_commits(repo, shas[0]) as clone:
        assert clone == tmp_path / "repos/o/shop" and clones.list_held() == {clone}
        assert git(clone, "cat-file", "-t", shas[0]) == "commit"
    assert clones.list_held() == set() and asked == [("o", "shop")]
    with clones.open_commits(repo, shas[0]):  # there already: nothing fetched
        pass
    assert len(asked) == 1
    with clones.open_commits(repo, shas[0], shas[2]):
        pass
    assert len(asked) == 2


def counting(calls: list):
    def run(fn, *args, progress):
        calls.append(fn.__name__)
        return run_here(fn, *args, progress=progress)
    return run


def test_a_change_is_built_once_and_its_commits_are_reused(src, tmp_path):
    path, shas = src
    store, sources = DirectoryStore(tmp_path / "data"), LocalRepository(path)
    repo = Repo(str(path))
    calls: list[str] = []
    first = repo.make_change(shas[0], shas[1])
    m = fetch_change_model(store, sources, first, run=counting(calls), about={"title": "a cache", "budget": 12})
    assert calls == ["extract_commit", "extract_commit", "diff_commits"]
    assert m.meta["diff"]["title"] == "a cache" and m.meta["budget"] == 12 and m.meta["key"]["base"] == shas[0]
    assert m.has("repo:shop/cache.py") and m.entities["repo:shop/cache.py"].attrs["diff"]["status"] == "added"
    assert (store.locate(first) / "review.md").is_file()  # what an agent run from the command line reads

    calls.clear()
    assert fetch_change_model(store, sources, first, run=counting(calls)).meta["diff"]["title"] == "a cache"
    assert calls == []  # stored: nothing built
    fetch_change_model(store, sources, repo.make_change(shas[1], shas[2]), run=counting(calls))
    assert calls == ["extract_commit", "diff_commits"]  # its base is the first change's head


def test_extract_commit_names_the_root(src):
    path, shas = src
    from nolli.progress import Quiet

    m = extract_commit(path, shas[0], "shop", progress=Quiet())
    assert m.entities["repo:"].name == "shop" and m.meta["commit"] == shas[0][:7] and not m.has("repo:shop/cache.py")
