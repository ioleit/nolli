"""Extract every repository in dev/corpus.local.toml and report the timings.

    python -I dev/extract_corpus.py [dev/corpus.local.toml]

Datasets are written to <data_dir>/<name>-<short sha> (the sha of the repository's
HEAD, or "wip" when it has uncommitted changes); older datasets of the same name are
removed so the viewer shows one per repository.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha_of(repo: Path) -> str:
    try:
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
        if dirty:
            return "wip"
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return "nogit"


def main() -> int:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "corpus.local.toml"
    if not cfg_path.exists():
        print(f"no {cfg_path}; copy dev/corpus.example.toml and edit the paths", file=sys.stderr)
        return 2
    cfg = tomllib.loads(cfg_path.read_text())
    data_dir = Path(cfg.get("data_dir", "~/data/nolli")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    nolli = HERE.parent / ".venv" / "bin" / "nolli"
    total0 = time.time()
    for r in cfg.get("repo", []):
        repo = (HERE.parent / r["path"]).resolve() if r["path"] == "." else Path(r["path"]).expanduser().resolve()
        name = r["name"]
        out = data_dir / f"{name}-{sha_of(repo)}"
        for old in data_dir.glob(f"{name}-*"):
            # only this repository's earlier extracts (<name>-<short sha>, -wip, -nogit); other
            # datasets that merely share the prefix (a change named <name>-pr48) stay
            if old != out and re.fullmatch(r"[0-9a-f]{7,12}|wip|nogit", old.name[len(name) + 1:]):
                shutil.rmtree(old)
        t0 = time.time()
        res = subprocess.run([str(nolli), "extract", str(repo), "-o", str(out)], capture_output=True, text=True)
        dt = time.time() - t0
        if res.returncode != 0:
            print(f"FAILED {name} ({dt:.1f}s)\n{res.stderr[-800:]}")
            continue
        print(f"{name:20s} {dt:5.1f}s  -> {out.name}")
    print(f"total {time.time() - total0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
