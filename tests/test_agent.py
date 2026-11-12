"""nolli agent brief / check / skill on a small repository with one branch: the brief
names its files, a verdict on a drawn box and edge passes the check, wrong ones fail it."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from nolli.cli import main
from nolli.review.verdicts import _parse_verdicts
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


def test_check(change, capsys):
    mmd = Path(change["brief"]).read_text()  # the brief holds the diagram the verdicts answer
    edge = next(g for line in mmd.splitlines() if (g := re.match(r"\s*(n_\w+) -->\S* (n_\w+)$", line)))
    good = [{"target": "n_repo_shop_cache_py", "verdict": "fine", "reason": "a cache module"},
            {"target": f"{edge[1]} --> {edge[2]}", "verdict": "question", "reason": "why?"}]
    Path(change["verdicts"]).write_text(json.dumps(good))
    assert main(shlex.split(change["check"])[1:]) == 0  # the command brief printed
    assert json.loads(capsys.readouterr().out) == {"ok": True, "verdicts": 2, "blunder": 0, "problem": 0, "question": 1,
                                                    "brilliant": 0, "good": 0, "fine": 1, "errors": 0}
    data = Path(change["verdicts"]).parent.parent.parent  # passing verdicts are the change's review, in the store
    key = change["key"]
    store = RepoStoreProvider(data).open(ModelKey.parse(key).repo)
    assert store.open_maps("verdicts").load_about(key)["by"] == "agent"
    assert [v["verdict"] for v in _parse_verdicts(store.open_maps("verdicts").load(key))] == ["fine", "question"]
    bad = [{"target": f"{edge[2]} --> {edge[1]}", "verdict": "fine", "reason": "x"},
           {"target": "n_repo_shop_nope_py", "verdict": "bad", "reason": ""}]
    Path(change["verdicts"]).write_text(json.dumps(bad))
    assert main(["agent", "check", change["verdicts"]]) == 1
    err = capsys.readouterr().err
    assert "draws no edge" in err and "is not a node" in err and "must be one of" in err and "needs a reason" in err
    assert len(_parse_verdicts(store.open_maps("verdicts").load(key))) == 2  # failing verdicts replace no review


def test_skill(tmp_path, capsys):
    assert main(["agent", "skill"]) == 0
    assert capsys.readouterr().out.startswith("---\nname: nolli-review\n")
    assert main(["agent", "skill", "--install", str(tmp_path / "skills")]) == 0
    assert (tmp_path / "skills/nolli-review/SKILL.md").is_file()
