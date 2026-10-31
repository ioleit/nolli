"""nolli agent brief / check / skill on a small repository with one branch: the brief
names its files, a verdict on a drawn box and edge passes the check, wrong ones fail it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from nolli.cli import main


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
    assert Path(change["verdicts"]).parent == Path(change["dataset"])


def test_check(change, capsys):
    mmd = (Path(change["dataset"]) / "review.mmd").read_text()
    edge = next(g for line in mmd.splitlines() if (g := re.match(r"\s*(n_\w+) -->\S* (n_\w+)$", line)))
    good = [{"target": "n_fs_shop_cache_py", "verdict": "fine", "reason": "a cache module"},
            {"target": f"{edge[1]} --> {edge[2]}", "verdict": "question", "reason": "why?"}]
    Path(change["verdicts"]).write_text(json.dumps(good))
    assert main(["agent", "check", change["dataset"]]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "verdicts": 2, "problem": 0, "question": 1, "fine": 1, "errors": 0}
    bad = [{"target": f"{edge[2]} --> {edge[1]}", "verdict": "fine", "reason": "x"},
           {"target": "n_fs_shop_nope_py", "verdict": "bad", "reason": ""}]
    Path(change["verdicts"]).write_text(json.dumps(bad))
    assert main(["agent", "check", change["verdicts"]]) == 1
    err = capsys.readouterr().err
    assert "draws no edge" in err and "is not a node" in err and "must be one of" in err and "needs a reason" in err


def test_skill(tmp_path, capsys):
    assert main(["agent", "skill"]) == 0
    assert capsys.readouterr().out.startswith("---\nname: nolli-review\n")
    assert main(["agent", "skill", "--install", str(tmp_path / "skills")]) == 0
    assert (tmp_path / "skills/nolli-review/SKILL.md").is_file()
