"""nolli agent brief / check / skill, and nolli comments, on a small repository with one branch:
the brief names its files, a verdict on a drawn box and edge passes the check and is kept as the
agent's comment on the change, wrong ones fail it."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from nolli.cli import main
from nolli.model.keys import ModelKey
from nolli.store import RepoStoreProvider


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def change(tmp_path: Path, capsys) -> dict:
    r = tmp_path / "shop"
    (r / "shop").mkdir(parents=True)
    (r / "shop/__init__.py").write_text("")
    (r / "shop/store.py").write_text("class Store:\n    def get(self, k):\n        return k\n")
    (r / "shop/api.py").write_text("from .store import Store\n\nS = Store()\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "shop")
    git(r, "checkout", "-q", "-b", "cache")
    (r / "shop/cache.py").write_text("from .store import Store\n\nclass Cache:\n    def __init__(self):\n        self.s = Store()\n")
    (r / "shop/api.py").write_text("from .cache import Cache\nfrom .store import Store\n\nS = Store()\nC = Cache()\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "Add a cache in front of the store")
    assert main(["agent", "brief", "--repo", str(r), "--data", str(tmp_path / "data")]) == 0
    return json.loads(capsys.readouterr().out)


def test_brief(change):
    assert change["title"] == "Add a cache in front of the store"
    assert Path(change["brief"]).read_text().startswith("# Architecture review: Add a cache in front of the store")
    assert not change["has_verdicts"]
    work = Path(change["verdicts"]).parent  # the agent's own directory, beside the store, with the change's key
    assert work == Path(change["brief"]).parent and work.parent.name == ".agent" and (work / "key").read_text().strip() == change["key"]
    assert isinstance(ModelKey.parse(change["key"]), ModelKey) and change["base"] in change["key"]
    assert shlex.split(change["check"])[:4] == ["nolli", "agent", "check", change["key"]] and "--data" in change["check"]
    assert change["dataset"] == change["key"] and "out of date" in change["dataset_note"]  # what a skill from before 0.10 reads
    assert change["observed"].startswith(change["key"] + "~project:")  # the projection the brief draws


def test_check(change, capsys):
    mmd = Path(change["brief"]).read_text()  # the brief holds the diagram the verdicts answer
    edge = next(g for line in mmd.splitlines() if (g := re.match(r"\s*(n_\w+) -->\S* (n_\w+)$", line)))
    good = [{"target": "n_repo_shop_cache_py", "verdict": "fine", "reason": "a cache module"},
            {"target": f"{edge[1]} --> {edge[2]}", "verdict": "question", "reason": "why?"}]
    Path(change["verdicts"]).write_text(json.dumps(good))
    assert main(shlex.split(change["check"])[1:]) == 0  # the command brief printed
    assert json.loads(capsys.readouterr().out) == {"ok": True, "verdicts": 2, "blunder": 0, "problem": 0, "question": 1,
                                                    "brilliant": 0, "good": 0, "fine": 1, "errors": 0}
    data = Path(change["verdicts"]).parent.parent.parent  # passing verdicts are the agent's comments on the change, in the store
    key = ModelKey.parse(change["key"])
    store = RepoStoreProvider(data).open(key.repo)
    kept = store.load_comments(key)
    assert [(c["verdict"], c["by"], c["observed"]) for c in kept] == [("fine", "agent", change["observed"]), ("question", "agent", change["observed"])]
    assert kept[0]["target"] == "repo:shop/cache.py" and kept[1]["target"].startswith("use|")  # ids of the projection
    bad = [{"target": f"{edge[2]} --> {edge[1]}", "verdict": "fine", "reason": "x"},
           {"target": "n_repo_shop_nope_py", "verdict": "bad", "reason": ""}]
    Path(change["verdicts"]).write_text(json.dumps(bad))
    assert main(["agent", "check", change["verdicts"]]) == 1
    err = capsys.readouterr().err
    assert "draws no edge" in err and "is not a node" in err and "must be one of" in err and "needs a reason" in err
    assert len(store.load_comments(key)) == 2  # failing verdicts replace no review


def test_skill(tmp_path, capsys):
    assert main(["agent", "skill"]) == 0
    assert capsys.readouterr().out.startswith("---\nname: nolli-review\n")
    assert main(["agent", "skill", "--install", str(tmp_path / "skills")]) == 0
    assert (tmp_path / "skills/nolli-review/SKILL.md").is_file()


def test_comments(change, tmp_path, capsys):
    """Anyone's comments, on any model by its key, kept beside the agent's: a new batch replaces only
    the same author's on the same model."""
    data = Path(change["verdicts"]).parent.parent.parent
    key = ModelKey.parse(change["key"])
    store = RepoStoreProvider(data).open(key.repo)
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps([{"target": "repo:shop/cache.py", "text": "Why a module of its own?"}]))
    assert main(["comments", "add", change["key"], str(mine), "--by", "ana", "--data", str(data)]) == 0
    assert main(["comments", "add", change["observed"], str(mine), "--by", "ana", "--data", str(data)]) == 0
    assert [(c["by"], c["observed"]) for c in store.load_comments(key)] == [("ana", change["key"]), ("ana", change["observed"])]
    mine.write_text(json.dumps([{"target": "repo:shop/nope.py", "text": "x"}]))
    assert main(["comments", "add", change["key"], str(mine), "--by", "ana", "--data", str(data)]) == 1
    assert "is not a node" in capsys.readouterr().err and len(store.load_comments(key)) == 2  # nothing kept
    mine.write_text(json.dumps([{"target": "provide|api:|repo:shop|route", "text": "x"}]))  # an edge only a projection draws
    assert main(["comments", "add", change["key"], str(mine), "--data", str(data)]) == 1
    assert "is no edge of this model" in capsys.readouterr().err
    assert main(["comments", "list", change["observed"], "--data", str(data)]) == 0
    assert [c["text"] for c in json.loads(capsys.readouterr().out)] == ["Why a module of its own?"] * 2
