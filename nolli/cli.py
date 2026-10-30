"""nolli command line.

    nolli extract <repo> -o <out_dir> [--budget N]

Writes model.json (all facts), view.json (the default cut), scene.json (the
scene of that cut, exactly what the viewer's /api/scene returns for it),
default.mmd (Mermaid of that cut) and report.json (counts, unresolved
references, extractor notes).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import match
from .extractors import Context, all_extractors
from .mermaid import render
from .model import Model
from .scene import ALL_ROLES, DEFAULT_ROLES, ViewState, compute, scene


def tracked_files(repo: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
        files = [Path(p.decode("utf-8", "surrogateescape")) for p in out.split(b"\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(repo) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [f for f in files if (repo / f).is_file()]


def git_head(repo: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def extract(repo: Path, config: dict | None = None) -> tuple[Model, Context]:
    m = Model()
    ctx = Context(repo=repo, model=m, files=tracked_files(repo), config=config or {})
    timings = {}
    for name, fn in all_extractors():
        t0 = time.time()
        fn(ctx)
        timings[name] = round(time.time() - t0, 3)
    t0 = time.time()
    match.run_all(ctx)
    timings["match"] = round(time.time() - t0, 3)
    m.meta = {"repo": str(repo), "commit": git_head(repo), "files": len(ctx.files), "timings": timings,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return m, ctx


def cmd_extract(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    m, ctx = extract(repo)
    m.save(out / "model.json")
    st = ViewState(budget=args.budget)
    expanded, _hidden, visible, rolled, _utilities = compute(m, st)
    view = {"budget": args.budget, "expanded": sorted(expanded), "visible": sorted(visible),
            "hidden_roles": [r for r in ALL_ROLES if r not in DEFAULT_ROLES]}
    (out / "view.json").write_text(json.dumps(view, indent=1))
    (out / "scene.json").write_text(json.dumps(scene(m, st), indent=1))
    (out / "rollup.json").unlink(missing_ok=True)  # replaced by scene.json
    (out / "default.mmd").write_text(render(m, view, rolled))
    report = {
        "meta": m.meta,
        "summary": m.summary(),
        "view": {"visible": len(view["visible"]), "edges": len(rolled.edges), "internal": rolled.internal,
                 "hidden_relations": rolled.hidden_relations, "unresolved": rolled.unresolved},
        "unresolved": sorted(e.id for e in m.entities.values() if e.kind == "unresolved"),
        "notes": {k: v for k, v in ctx.notes.items() if k not in ("py-imports", "js-imports")},
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"entities": report["summary"]["entities"], "relations": report["summary"]["relations"],
                      "view": {"visible": len(view["visible"]), "edges": len(rolled.edges)},
                      "unresolved": len(report["unresolved"])}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nolli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="extract the model of a repository")
    ex.add_argument("repo")
    ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--budget", type=int, default=34)
    ex.set_defaults(fn=cmd_extract)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
