"""nolli in a repository's CI: what a pull request's review run leaves behind, and how
``nolli view`` finds it again.

On every push to a pull request, the workflow (``.github/workflows/nolli.yml``) builds the
change's dataset, lets a review agent write its verdicts, uploads the dataset as a workflow
artifact named ``nolli-pr<n>-<head7>`` and keeps one comment on the PR up to date
(``nolli ci comment``). The comment names the result by its key,
``<owner>/<repo>#<n>@<head7>``; ``nolli view <key>`` downloads that artifact with the
viewer's own GitHub login, so the repository's access decides who may see it. The run
deletes the PR's older results, and all of them when the PR closes.

The comment also carries the verdicts and what they were made on (base, head, nolli's
version), hidden, so they outlive the artifact.
"""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import tempfile
import zipfile
import zlib
from pathlib import Path

from .change import ChangeError
from .model import Model

KEY = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)@([0-9a-f]{7,40})$")
MARKER = "<!-- nolli -->"
RESULT = re.compile(r"<!-- nolli-result (\S+) -->")
GLYPH = {"blunder": "??", "problem": "?", "question": "?!", "brilliant": "!!", "good": "👍", "fine": "✓"}
WORST_FIRST = ("blunder", "problem", "question", "brilliant", "good", "fine")
LISTED = 5  # verdicts listed in the comment, worst first; fine ones never
MERMAID_MAX = 40_000  # characters of the review diagram's Mermaid; a comment holds 65536
# the verdict marks as the viewer draws them (assets/verdicts/), images on GitHub; $NOLLI_ASSETS_URL for another copy
ASSETS = "https://raw.githubusercontent.com/ioleit/nolli/main/assets/verdicts"
NEW = {"added": "New", "removed": "Removed", "changed": "Changed"}


def is_key(spec: str | None) -> bool:
    return bool(spec and KEY.match(spec))


def artifact_name(pr: int | str, head: str) -> str:
    return f"nolli-pr{pr}-{head[:7]}"


def key_of(meta: dict) -> str:
    """A PR dataset's key, from what its model records (remote, PR number, head)."""
    d = meta.get("diff") or {}
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", d.get("remote") or "")
    if not m or not d.get("pr"):
        raise ValueError("not the dataset of a GitHub pull request (no remote or PR number in its model)")
    return f"{m.group(1)}/{m.group(2)}#{d['pr']}@{d['head'][:7]}"


def comment(ds: Path, skipped: str | None = None) -> str:
    """The PR comment for a run's dataset: how many structural changes, how many verdicts of
    each kind, the worst ones (not fine) with their reasons, and how to open the result.
    ``skipped`` says why no review agent ran: "no-key" (the repository has no API key for
    one) or "fork" (a PR from a fork, which gets no secrets)."""
    from .review import check_verdicts, diagram_changes, resolve_verdicts

    m = Model.load(ds / "model.json")
    key = key_of(m.meta)
    d = m.meta["diff"]
    repo = key.split("#")[0]
    n = len(json.loads((ds / "changes.json").read_text()))
    vf = ds / "verdicts.json"
    verdicts, errors = check_verdicts(m, (ds / "review.mmd").read_text(), vf.read_text()) if vf.is_file() else (None, [])
    commit = f"[`{d['head'][:7]}`](https://github.com/{repo}/pull/{d['pr']}/commits/{d['head']})"
    lines = [MARKER, f"**Nolli** found {n} structural change{'' if n == 1 else 's'} in this PR (at {commit})."]
    after: list[str] = []  # why there is no review, after everything else
    if skipped == "no-key":
        after = ["Want an automatic structural review from an AI agent? Add an API key:", "",
                 "```", f"gh secret set ANTHROPIC_API_KEY --repo {repo}", "```"]
        verdicts = None
    elif skipped == "fork":
        after = ["No review: pull requests from forks get no API key for it."]
        verdicts = None
    elif verdicts is None or errors:
        after = [f"No review: {'the review agent wrote no verdicts' if verdicts is None else 'the review agent’s verdicts failed nolli’s check'}."]
        verdicts = None
    else:
        verdicts = resolve_verdicts(m, verdicts)
        status = diagram_changes((ds / "review.mmd").read_text())
        total = len(verdicts)
        counts = " &nbsp; ".join(f"{mark(k)} {c}" for k in WORST_FIRST if (c := sum(v["verdict"] == k for v in verdicts)))
        lines[-1] += f" The review agent's {total} verdict{'' if total == 1 else 's'}: {counts}"
        listed = sorted((v for v in verdicts if v["verdict"] != "fine"), key=lambda v: WORST_FIRST.index(v["verdict"]))
        if listed:
            lines.append("")
            lines += [f"- {mark(v['verdict'])} **{what(m, v, status)}**. {' '.join(v['reason'].split())}" for v in listed[:LISTED]]
            if len(listed) > LISTED:
                lines.append(f"- {len(listed) - LISTED} more")
    lines += ["", "Run these commands for the interactive viewer:", "",
              "```", "pip install nolli-review", f"nolli view {key}", "```"]
    mmd = ds / "review.mmd"
    if mmd.is_file() and len(mmd.read_text()) <= MERMAID_MAX:
        lines += ["", "<details><summary>Mermaid of all of the changes (experimental)</summary>", "", "```mermaid",
                  mmd.read_text().rstrip(), "```", "", "</details>"]
    if after:
        lines += ["", *after]
    result = {"key": key, "base": d["base"], "head": d["head"], "nolli": m.meta.get("nolli"),
              "verdicts": [{k: v[k] for k in ("target", "verdict", "reason")} for v in verdicts or []]}
    packed = base64.b64encode(zlib.compress(json.dumps(result, separators=(",", ":")).encode(), 9)).decode()
    lines += ["", f"<!-- nolli-result {packed} -->"]
    return "\n".join(lines) + "\n"


def mark(verdict: str) -> str:
    """A verdict's mark as an image in the comment, its glyph as the alternative text."""
    import os

    base = os.environ.get("NOLLI_ASSETS_URL") or ASSETS
    return f'<img src="{base}/{verdict}.svg" height="18" alt="{GLYPH[verdict]}" title="{verdict}" align="top">'


def what(m: Model, v: dict, status: dict[str, str]) -> str:
    """What a verdict is about, in words: "New dependency from `a` to `b`", "Changed module `x`"."""
    from .changes import short
    from .labels import is_body, is_module

    ids = [t.removesuffix("_g") for t in re.findall(r"n_\w+", v["target"])]
    st = NEW.get(status.get(" ".join(ids), ""), "")
    def name(x: str) -> str:
        return f"`{short(m, x[5:] if is_body(x) else x)}`"
    if v["edge"]:
        a, b = v["ends"]
        return f"{st} dependency from {name(a)} to {name(b)}" if st else f"Dependency from {name(a)} to {name(b)}"
    if not v["ends"]:
        return v["label"]
    x = v["ends"][0]
    if is_body(x):
        return f"Own code of {name(x)}"
    e = m.entities[x]
    kind = "module" if is_module(e) else "package" if e.kind == "directory" else e.kind
    return f"{st} {kind} {name(x)}" if st else f"{kind.capitalize()} {name(x)}"


def read_result(body: str) -> dict | None:
    """What a nolli comment carries hidden (see comment()), or None."""
    m = RESULT.search(body)
    return json.loads(zlib.decompress(base64.b64decode(m.group(1)))) if m else None


def fetch(key: str, data: Path) -> Path:
    """The dataset a CI run stored under ``key``, downloaded into ``data`` (once)."""
    from .github import api, download, token

    owner, repo, pr, head = KEY.match(key).groups()
    name = artifact_name(pr, head)
    out = data / f"{repo}-ci-pr{pr}-{head[:7]}"
    if (out / "model.json").is_file():
        return out
    t = token()
    if not t:
        raise ChangeError("a CI result is downloaded with your GitHub login: log in with gh (gh auth login), or set GH_TOKEN")
    live = [a for a in api(f"repos/{owner}/{repo}/actions/artifacts?name={name}&per_page=10")["artifacts"] if not a["expired"]]
    if not live:
        raise ChangeError(f"{key}: GitHub keeps no result for it any more (artifact {name}: expired, or replaced by a later "
                          f"push); the PR's current state: nolli view https://github.com/{owner}/{repo}/pull/{pr}")
    blob = download(live[0]["archive_download_url"], t)
    with tempfile.TemporaryDirectory(prefix="nolli-ci-", dir=data if data.is_dir() else None) as tmp:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for info in z.infolist():
                p = Path(info.filename)
                if p.is_absolute() or ".." in p.parts:
                    raise ChangeError(f"{key}: the artifact has a file outside its directory: {info.filename}")
            z.extractall(tmp)
        if not (Path(tmp) / "model.json").is_file():
            raise ChangeError(f"{key}: artifact {name} holds no nolli dataset")
        data.mkdir(parents=True, exist_ok=True)
        shutil.move(tmp, out)
    return out
