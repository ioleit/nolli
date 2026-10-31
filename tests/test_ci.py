"""nolli in CI (nolli/ci.py): the key a PR comment gives, and the comment itself, for a
change without a review, with one, and with verdicts that fail the check. Downloading an
artifact is not tested here: it needs GitHub."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from nolli.ci import is_key, key_of, read_result
from nolli.cli import main
from nolli.model import Model


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def dataset(tmp_path: Path, capsys) -> Path:
    """A change's dataset as a CI run builds it for PR #7 of me/shop."""
    r = tmp_path / "shop"
    (r / "shop").mkdir(parents=True)
    (r / "shop/__init__.py").write_text("")
    (r / "shop/store.py").write_text("class Store:\n    pass\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "shop")
    git(r, "checkout", "-q", "-b", "cache")
    (r / "shop/cache.py").write_text("from .store import Store\n\nclass Cache:\n    s = Store()\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "Add a cache")
    assert main(["agent", "brief", "--repo", str(r), "--data", str(tmp_path / "data")]) == 0
    ds = Path(json.loads(capsys.readouterr().out)["dataset"])
    m = json.loads((ds / "model.json").read_text())  # as if built from '#7' on GitHub
    m["meta"]["diff"] |= {"remote": "https://github.com/me/shop", "pr": 7}
    (ds / "model.json").write_text(json.dumps(m))
    return ds


def comment(ds: Path, capsys) -> str:
    assert main(["ci", "comment", str(ds)]) == 0
    return capsys.readouterr().out


def test_key():
    assert is_key("ioleit/nolli#25@a34bf10") and is_key("o/r.js#1@" + "0" * 40)
    for spec in ("#25", "ioleit/nolli#25", "ioleit/nolli@a34bf10", "https://github.com/o/r/pull/1", "o/r#x@a34bf10"):
        assert not is_key(spec), spec


def test_without_review(dataset, capsys):
    key = key_of(Model.load(dataset / "model.json").meta)
    assert re.fullmatch(r"me/shop#7@[0-9a-f]{7}", key)
    text = comment(dataset, capsys)
    assert text.startswith("<!-- nolli -->\n**Nolli** found ")
    assert re.search(r"found \d+ structural changes? in this PR \(at \[`[0-9a-f]{7}`\]\(https://github.com/me/shop/pull/7/commits/", text)
    assert "No review: the review agent wrote no verdicts." in text
    assert f"nolli view {key}\n" in text
    assert "```mermaid\nflowchart" in text
    assert read_result(text)["verdicts"] == [] and read_result(text)["key"] == key


def test_skipped(dataset, capsys):
    assert main(["ci", "comment", str(dataset), "--skipped", "no-key"]) == 0
    text = capsys.readouterr().out
    assert text.index("nolli view me/shop#7@") < text.index("<details><summary>") < text.index(
        "Want an automatic structural review from an AI agent? Add an API key:\n\n```\ngh secret set ANTHROPIC_API_KEY --repo me/shop\n```")
    assert "No review" not in text
    assert main(["ci", "comment", str(dataset), "--skipped", "fork"]) == 0
    assert "No review: pull requests from forks get no API key for it." in capsys.readouterr().out


def test_with_review(dataset, capsys):
    verdicts = [{"target": "n_fs_shop_cache_py", "verdict": "good", "reason": "a cache\nin its own module"},
                {"target": "n_fs_shop_cache_py --> n_fs_shop_store_py", "verdict": "question", "reason": "why --> here?"},
                {"target": "n_fs_shop_store_py", "verdict": "fine", "reason": "unchanged"}]
    (dataset / "verdicts.json").write_text(json.dumps(verdicts))
    text = comment(dataset, capsys)
    assert "The review agent marked these: 1 ?!, 1 👍." in text
    findings = [line for line in text.splitlines() if line.startswith("- ")]
    assert findings[0].startswith("- `?!` **") and findings[0].endswith(": why --> here?")  # worst first
    assert findings[1].startswith("- `👍` **shop/cache.py**: a cache in its own module")
    assert len(findings) == 2  # fine is not noteworthy
    assert [v["verdict"] for v in read_result(text)["verdicts"]] == ["good", "question", "fine"]  # "-->" kept, hidden


def test_failed_review(dataset, capsys):
    (dataset / "verdicts.json").write_text(json.dumps([{"target": "n_nope", "verdict": "good", "reason": "x"}]))
    text = comment(dataset, capsys)
    assert "No review: the review agent’s verdicts failed nolli’s check." in text
    assert "marked these" not in text


def test_not_a_pr(dataset, capsys):
    m = json.loads((dataset / "model.json").read_text())
    del m["meta"]["diff"]["pr"]
    (dataset / "model.json").write_text(json.dumps(m))
    assert main(["ci", "comment", str(dataset)]) == 2
    assert "not the dataset of a GitHub pull request" in capsys.readouterr().err
