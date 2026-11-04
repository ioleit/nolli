"""nolli in CI (nolli/review/ci.py): the key a PR comment gives, and the comment itself, for a
change without a review, with one, and with verdicts that fail the check. Downloading an
artifact is not tested here: it needs GitHub."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import zlib
from pathlib import Path

import pytest

from nolli.review.ci import is_key, key_of, read_result
from nolli.datasets import open_change_scene
from nolli.cli import main
from nolli.store import DirectoryStore


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
    edit_about(ds, lambda about: about | {"remote": "https://github.com/me/shop", "pr": 7})  # as if built from '#7' on GitHub
    return ds


def edit_about(ds: Path, edit) -> None:
    """Change what the dataset's change says about itself (change.json)."""
    c = json.loads((ds / "change.json").read_text())
    c["about"] = edit(c["about"])
    (ds / "change.json").write_text(json.dumps(c))


def review(ds: Path, verdicts: list[dict], capsys) -> None:
    """The review agent's verdicts, as the CI workflow keeps them: verdicts.json in the
    dataset's directory, then nolli agent check, which keeps them if they pass."""
    (ds / "verdicts.json").write_text(json.dumps(verdicts))
    main(["agent", "check", str(ds)])
    capsys.readouterr()


def open_scene(ds: Path):
    store = DirectoryStore(ds.parent)
    return open_change_scene(store, store.find_key(ds.name))


def comment(ds: Path, capsys) -> str:
    assert main(["ci", "comment", str(ds)]) == 0
    return capsys.readouterr().out


def test_key():
    assert is_key("ioleit/nolli#25@a34bf10") and is_key("o/r.js#1@" + "0" * 40)
    for spec in ("#25", "ioleit/nolli#25", "ioleit/nolli@a34bf10", "https://github.com/o/r/pull/1", "o/r#x@a34bf10"):
        assert not is_key(spec), spec


def test_without_review(dataset, capsys):
    key = key_of(open_scene(dataset).model.meta)
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
    verdicts = [{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "a cache\nin its own module"},
                {"target": "n_repo_shop_cache_py --> n_repo_shop_store_py", "verdict": "question", "reason": "why --> here?"},
                {"target": "n_repo_shop_store_py", "verdict": "fine", "reason": "unchanged"}]
    review(dataset, verdicts, capsys)
    text = comment(dataset, capsys)
    first = text.splitlines()[1]
    assert re.search(r"\. The review agent's 3 verdicts: <img src=\"\S+/question\.svg\"[^>]*> 1 &nbsp; <img [^>]*good\.svg[^>]*> 1 "
                     r"&nbsp; <img [^>]*fine\.svg[^>]*> 1$", first), first
    findings = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(findings) == 2  # never the fine one
    assert re.fullmatch(r'- <img [^>]*alt="\?!"[^>]*> \*\*New dependency from `shop/cache\.py` to `shop/store\.py`\*\*\. why --> here\?', findings[0])
    assert re.fullmatch(r"- <img [^>]*good\.svg[^>]*> \*\*New module `shop/cache\.py`\*\*\. a cache in its own module", findings[1])
    assert [v["verdict"] for v in read_result(text)["verdicts"]] == ["good", "question", "fine"]  # "-->" kept, hidden


def test_listed(dataset, capsys, monkeypatch):
    monkeypatch.setenv("NOLLI_ASSETS_URL", "https://example.com/marks")
    verdicts = [{"target": "n_repo_shop_store_py", "verdict": "question", "reason": f"q{i}"} for i in range(7)]
    review(dataset, verdicts + [{"target": "n_repo_shop_cache_py", "verdict": "fine", "reason": "ok"}], capsys)
    text = comment(dataset, capsys)
    findings = [line for line in text.splitlines() if line.startswith("- ")]
    assert findings[0].startswith('- <img src="https://example.com/marks/question.svg"')
    assert findings[0].endswith("**Module `shop/store.py`**. q0")  # drawn, not changed
    assert len(findings) == 6 and findings[-1] == "- 2 more"
    review(dataset, [{"target": "n_repo_shop_cache_py", "verdict": "fine", "reason": "ok"}], capsys)
    text = comment(dataset, capsys)
    assert not [line for line in text.splitlines() if line.startswith("- ")]
    assert "The review agent's 1 verdict: <img" in text


def test_failed_review(dataset, capsys):
    from nolli.review.verdicts import keep_verdicts_file

    review(dataset, [{"target": "n_nope", "verdict": "good", "reason": "x"}], capsys)  # nolli agent check keeps none of it
    assert "No review: the review agent wrote no verdicts." in comment(dataset, capsys)
    # kept without the check (the edge is drawn the other way round): the comment checks them again
    store = DirectoryStore(dataset.parent)
    key = store.find_key(dataset.name)
    wrong = [{"target": "n_repo_shop_store_py --> n_repo_shop_cache_py", "verdict": "good", "reason": "x"}]
    assert keep_verdicts_file(store, key, open_scene(dataset), json.dumps(wrong), {"by": "upload"})[0]["ends"]
    text = comment(dataset, capsys)
    assert "No review: the review agent’s verdicts failed nolli’s check." in text
    assert "verdicts:" not in text


def test_not_a_pr(dataset, capsys):
    edit_about(dataset, lambda about: {k: v for k, v in about.items() if k != "pr"})
    assert main(["ci", "comment", str(dataset)]) == 2
    assert "not the dataset of a GitHub pull request" in capsys.readouterr().err


def bot_comment(text: str, login: str = "nolli-review[bot]", n: int = 1) -> dict:
    return {"user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}, "body": text,
            "html_url": f"https://github.com/me/shop/pull/7#issuecomment-{n}", "updated_at": f"2026-11-0{n}T00:00:00Z"}


def test_results_from_the_comments(dataset, capsys):
    from nolli.review.ci import results

    review(dataset, [{"target": "n_repo_shop_cache_py", "verdict": "good", "reason": "ok"}], capsys)
    text = comment(dataset, capsys)
    head = json.loads((dataset / "change.json").read_text())["about"]["head"]
    forged = bot_comment(text, login="mallory", n=3)  # anyone can paste a comment like the bot's
    found = results([bot_comment(text, n=1), bot_comment("<!-- nolli -->\nno result", n=2), forged])
    assert [r["link"] for r in found] == ["https://github.com/me/shop/pull/7#issuecomment-1"]
    assert found[0]["head"] == head and found[0]["verdicts"][0]["verdict"] == "good"
    r = read_result(text) | {"head": "f" * 40}
    packed = base64.b64encode(zlib.compress(json.dumps(r).encode())).decode()
    later = bot_comment(f"<!-- nolli -->\nlater\n<!-- nolli-result {packed} -->", n=4)
    assert [r["head"] for r in results([bot_comment(text, n=1), later])] == ["f" * 40, head]  # newest first

