"""nolli command line.

    nolli view [<change> | <dataset dir>] [--whole] [--repo DIR] [--data DIR] [--port N] [--no-open] [--rebuild]
    nolli agent brief [<change>] | agent check <dataset> | agent skill [--install DIR]
    nolli extract <repo> -o <out_dir> [--budget N]
    nolli diff <repo> <base> <head> -o <out_dir> [--budget N] [--pr N] [--title T] [--description FILE]

view shows a change of the git repository you are in, or of --repo: by default the
current branch against the default branch; <change> can be a branch or commit (what it
adds to the default branch), <base>..<head> or <base>...<head> (see nolli/change.py), or
a GitHub pull request: its URL, or '#<n>' on the repository's GitHub remote (nolli/github.py).
It builds the change's dataset under --data (default: $NOLLI_DATA, else
$XDG_DATA_HOME/nolli or ~/.local/share/nolli), reusing one built before, then serves the
viewer on it and opens it in the browser.

agent is for a review agent (the skill, nolli agent skill): brief builds the dataset and
prints where review.md (what to do) is and where verdicts.json goes; check validates the
verdicts against the diagram they answer.

extract and diff write a dataset to a directory of your choice.

Writes model.json (all facts), view.json (the default cut), scene.json (the
scene of that cut, exactly what the viewer's /api/scene returns for it),
default.mmd (Mermaid of that cut) and report.json (counts, unresolved
references, extractor notes). diff also writes changes.json (the numbered
changes), review.mmd (Mermaid with every change in source code unfolded) and
review.md (what a review agent gets: that diagram, how to answer, and the PR's
description from --description, or pr.md in the output directory); its answer
goes into verdicts.json next to it, which the viewer shows.
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
from .progress import Progress, Quiet
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


def extract(repo: Path, config: dict | None = None, progress: Progress | None = None) -> tuple[Model, Context]:
    m = Model()
    progress = progress or Quiet()
    ctx = Context(repo=repo, model=m, files=tracked_files(repo), config=config or {}, progress=progress)
    timings = {}
    for name, fn in all_extractors():
        t0 = time.time()
        with progress.step("file tree" if name == "fs" else name.replace("-", " ")):
            fn(ctx)
        timings[name] = round(time.time() - t0, 3)
    t0 = time.time()
    with progress.step("matching references"):
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
    report = write_dataset(m, out, args.budget, ctx.notes)
    print(json.dumps({"entities": report["summary"]["entities"], "relations": report["summary"]["relations"],
                      "view": {"visible": report["view"]["visible"], "edges": report["view"]["edges"]},
                      "unresolved": len(report["unresolved"])}, indent=1))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    out = Path(args.out)
    desc = Path(args.description) if args.description else out / "pr.md"
    m, summary, report = build_diff(Path(args.repo), args.base, args.head, out, args.budget, pr=args.pr, title=args.title,
                                    description=desc.read_text() if desc.is_file() else None)
    print(json.dumps({"diff": m.meta["diff"] | {"base": m.meta["diff"]["base_short"], "head": m.meta["diff"]["head_short"]},
                      "changes": summary, "view": {"visible": report["view"]["visible"], "edges": report["view"]["edges"]}}, indent=1))
    return 0


def build_diff(repo: Path, base: str, head: str, out: Path, budget: int, pr: int | None = None, title: str | None = None,
               description: str | None = None, remote: str | None = None,
               progress: Progress | None = None) -> tuple[Model, dict, dict]:
    """The dataset of a change: the merged model, changes.json, review.mmd and review.md."""
    from .changes import change_items
    from .diff import diff
    from .review import review_expanded, review_markdown

    progress = progress or Quiet()
    out.mkdir(parents=True, exist_ok=True)
    m, summary = diff(repo, base, head, extract, progress)
    m.meta["nolli"] = version()
    if remote:
        m.meta["diff"]["remote"] = remote
    if pr:
        m.meta["diff"]["pr"] = pr
    if title:
        m.meta["diff"]["title"] = title
    report = write_dataset(m, out, budget, {}, {"diff": summary}, progress)
    with progress.step("the review's diagram"):
        (out / "changes.json").write_text(json.dumps(change_items(m), indent=1))
        st = ViewState(budget=budget, expanded=review_expanded(m))
        expanded, _hidden, visible, rolled, _utilities = compute(m, st)
        mmd = render(m, {"budget": budget, "expanded": sorted(expanded), "visible": sorted(visible)}, rolled)
        (out / "review.mmd").write_text(mmd)
        (out / "review.md").write_text(review_markdown(m, mmd, description))
    return m, summary, report


def build_commit(repo: Path, sha: str, out: Path, budget: int, progress: Progress | None = None) -> dict:
    """The dataset of one commit as a whole, extracted from the commit (not the working tree)."""
    import tempfile

    from .diff import materialise

    progress = progress or Quiet()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nolli-commit-") as tmp:
        with progress.step("checking out the commit"):
            materialise(repo, sha, Path(tmp))
        m, ctx = extract(Path(tmp), progress=progress)
    if m.has("fs:."):  # the root is the temporary directory; name it after the repository
        m.entities["fs:."].name = repo.name
    m.meta.update({"repo": repo.name, "commit": sha[:7], "nolli": version()})
    return write_dataset(m, out, budget, ctx.notes, progress=progress)


def version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("nolli-review")
    except PackageNotFoundError:
        return "unknown"


def data_dir(arg: str | None) -> Path:
    """Where nolli view keeps datasets: --data, $NOLLI_DATA, $XDG_DATA_HOME/nolli or ~/.local/share/nolli."""
    import os

    if arg or os.environ.get("NOLLI_DATA"):
        return Path(arg or os.environ["NOLLI_DATA"]).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share").expanduser() / "nolli"


class Built:
    """A change's dataset, built or found: what view and the agent commands work on."""

    def __init__(self, repo: Path, change, host: dict, data: Path, name: str):
        self.repo, self.change, self.host, self.data, self.name = repo, change, host, data, name
        self.out = data / name


def build(args: argparse.Namespace, cmd: str) -> Built | None:
    """Resolve args.change in args.repo and build its dataset unless a fresh one exists.
    Progress goes to stderr; None (after saying why) if the change cannot be resolved."""
    from . import github
    from .change import ChangeError, dataset_name, resolve, toplevel, whole

    host: dict = {}
    try:
        repo = toplevel(Path(args.repo).resolve())
        if getattr(args, "whole", False):
            ch = whole(repo, args.change or "HEAD")
        elif github.is_pull_request(args.change):
            ch, host = github.resolve(repo, args.change)
        else:
            ch = resolve(repo, args.change)
    except ChangeError as exc:
        print(f"nolli {cmd}: {exc}", file=sys.stderr)
        return None
    data = data_dir(args.data)
    b = Built(repo, ch, host, data, dataset_name(repo, ch, host.get("pr")))
    what = f"{ch.base[:7]}..{ch.head[:7]}" if ch.is_diff else f"{ch.head[:7]} (the whole repository)"
    print(f"{repo.name}: {what}  {ch.title}", file=sys.stderr)
    if args.rebuild or not fresh(b.out):
        print(f"building {b.out}", file=sys.stderr, flush=True)
        t0 = time.time()
        progress = Progress()
        if ch.is_diff:
            _m, _summary, report = build_diff(repo, ch.base, ch.head, b.out, args.budget, title=ch.title,
                                              description=ch.description or None, pr=host.get("pr"),
                                              remote=host.get("remote"), progress=progress)
            (b.out / "pr.md").write_text(ch.description)
        else:
            report = build_commit(repo, ch.head, b.out, args.budget, progress)
        print(f"built in {time.time() - t0:.1f} s: {counts(report)}", file=sys.stderr)
    return b


def counts(report: dict) -> str:
    """What a build found, in a few words (from report.json)."""
    ents, rels = report["summary"]["entities"], report["summary"]["relations"]
    parts = [f"{report['meta'].get('files') or ents.get('file', 0)} files"]
    parts += [f"{ents[k]} {k}{'es' if k == 'class' else 's'}" for k in ("class", "service") if ents.get(k)]
    parts.append(f"{sum(v for k, v in rels.items() if k != 'contain')} dependencies")
    return ", ".join(parts)


def cmd_view(args: argparse.Namespace) -> int:
    ds = Path(args.change).expanduser() if args.change else None
    if ds and (ds / "model.json").is_file():  # a dataset directory, as nolli agent brief prints it
        return serve(ds.resolve().parent, ds.resolve().name, args.host, args.port, not args.no_open)
    b = build(args, "view")
    if b is None:
        return 2
    if args.build_only:
        print(b.out)
        return 0
    return serve(b.data, b.name, args.host, args.port, not args.no_open)


def cmd_brief(args: argparse.Namespace) -> int:
    """nolli agent brief: build the change's dataset and say, as JSON on stdout, where the
    brief is, where the verdicts go and how to check them."""
    b = build(args, "agent brief")
    if b is None:
        return 2
    ch = b.change
    if not ch.is_diff:
        print("nolli agent brief: no change to review (nothing between the default branch and HEAD); "
              "name one: a branch, <base>..<head> or a PR", file=sys.stderr)
        return 2
    verdicts = b.out / "verdicts.json"
    print(json.dumps({
        "repository": str(b.repo), "base": ch.base, "head": ch.head, "title": ch.title, "dataset": str(b.out),
        "brief": str(b.out / "review.md"), "verdicts": str(verdicts), "has_verdicts": verdicts.is_file(),
        "inspect": f"git -C {b.repo} diff {ch.base[:12]} {ch.head[:12]}",
        "check": f"nolli agent check {b.out}",
        "view": f"nolli view {b.out}",
    }, indent=1))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """nolli agent check: verdicts.json against the diagram it answers; problems on stderr."""
    from .review import check_verdicts

    target = Path(args.dataset).expanduser()
    ds = target.parent if target.is_file() else target
    if not (ds / "model.json").is_file() or not (ds / "review.mmd").is_file():
        ds = data_dir(args.data) / args.dataset
    if not (ds / "review.mmd").is_file():
        print(f"nolli agent check: {args.dataset} is not a change's dataset (no review.mmd)", file=sys.stderr)
        return 2
    vf = target if target.is_file() else ds / "verdicts.json"
    if not vf.is_file():
        print(f"nolli agent check: no {vf}", file=sys.stderr)
        return 1
    verdicts, errors = check_verdicts(Model.load(ds / "model.json"), (ds / "review.mmd").read_text(), vf.read_text())
    for e in errors:
        print(e, file=sys.stderr)
    counts = {v: sum(x["verdict"] == v for x in verdicts) for v in ("problem", "question", "fine")}
    print(json.dumps({"ok": not errors, "verdicts": len(verdicts), **counts, "errors": len(errors)}))
    return 1 if errors else 0


def cmd_skill(args: argparse.Namespace) -> int:
    """nolli agent skill: print the review skill, or install it where an agent finds skills."""
    from importlib.resources import files

    text = files("nolli").joinpath("skill/SKILL.md").read_text()
    if not args.install:
        print(text, end="")
        return 0
    dest = Path(args.install).expanduser()
    dest = dest / "nolli-review" if dest.name != "nolli-review" else dest
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(text)
    print(dest / "SKILL.md")
    return 0


def fresh(out: Path) -> bool:
    """A dataset built before by this version of nolli."""
    try:
        return json.loads((out / "report.json").read_text())["meta"].get("nolli") == version()
    except (OSError, ValueError, KeyError):
        return False


def serve(data: Path, name: str, host: str, port: int, open_browser: bool) -> int:
    import webbrowser
    from urllib.parse import quote

    from .viewer.serve import make_server, preload

    try:
        srv = make_server(data, host, port)
    except OSError:  # the port is taken (another viewer, perhaps): any free one
        srv = make_server(data, host, 0)
    url = f"http://{host}:{srv.server_address[1]}/#d={quote(name)}"
    preload(data / name)  # while the browser starts: a big model takes seconds to read
    print(f"viewer: {url}  (Ctrl-C to stop)", file=sys.stderr, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def write_dataset(m: Model, out: Path, budget: int, notes: dict, extra: dict | None = None,
                  progress: Progress | None = None) -> dict:
    """model.json, view.json, scene.json, default.mmd and report.json for a model."""
    progress = progress or Quiet()
    with progress.step("writing model.json"):
        m.save(out / "model.json")
    with progress.step("the default cut"):
        st = ViewState(budget=budget)
        expanded, _hidden, visible, rolled, _utilities = compute(m, st)
        view = {"budget": budget, "expanded": sorted(expanded), "visible": sorted(visible),
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
        "notes": {k: v for k, v in notes.items() if k not in ("py-imports", "js-imports", "py-aliases")},
        **(extra or {}),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nolli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    vw = sub.add_parser("view", help="show a change of a git repository (or one commit as a whole) in the viewer")
    vw.add_argument("change", nargs="?", help="a branch or commit, <base>..<head> or <base>...<head>, or a GitHub PR's "
                    "URL or '#<n>' (default: the current branch against the default branch)")
    vw.add_argument("--repo", default=".", help="the repository (default: the one you are in)")
    vw.add_argument("--data", help="where datasets are kept (default: $NOLLI_DATA, else $XDG_DATA_HOME/nolli or ~/.local/share/nolli)")
    vw.add_argument("--budget", type=int, default=34)
    vw.add_argument("--whole", action="store_true", help="the whole repository at HEAD (or at <change>, a commit), not a change")
    vw.add_argument("--rebuild", action="store_true", help="build the dataset again even if it exists")
    vw.add_argument("--build-only", action="store_true", help="build the dataset, print its directory, don't serve")
    vw.add_argument("--host", default="127.0.0.1")
    vw.add_argument("--port", type=int, default=8720, help="default 8720; a free one if that is taken")
    vw.add_argument("--no-open", action="store_true", help="don't open the browser")
    vw.set_defaults(fn=cmd_view)
    ag = sub.add_parser("agent", help="for a review agent: the brief of a change, checking its verdicts, the skill")
    agsub = ag.add_subparsers(dest="agent_cmd", required=True)
    br = agsub.add_parser("brief", help="build a change's dataset and print (JSON) where its brief and verdicts are")
    br.add_argument("change", nargs="?", help="as for nolli view")
    for a in (br,):
        a.add_argument("--repo", default=".")
        a.add_argument("--data")
        a.add_argument("--budget", type=int, default=34)
        a.add_argument("--rebuild", action="store_true")
    br.set_defaults(fn=cmd_brief)
    ck = agsub.add_parser("check", help="check a change's verdicts.json against its diagram")
    ck.add_argument("dataset", help="the dataset directory (or its name under --data), or a verdicts file in it")
    ck.add_argument("--data")
    ck.set_defaults(fn=cmd_check)
    sk = agsub.add_parser("skill", help="print the review skill (SKILL.md), or install it")
    sk.add_argument("--install", metavar="DIR", help="a skills directory, e.g. ~/.claude/skills or .claude/skills; "
                    "writes DIR/nolli-review/SKILL.md")
    sk.set_defaults(fn=cmd_skill)
    ex = sub.add_parser("extract", help="extract the model of a repository")
    ex.add_argument("repo")
    ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--budget", type=int, default=34)
    ex.set_defaults(fn=cmd_extract)
    df = sub.add_parser("diff", help="extract two commits and merge them into one model that records what changed")
    df.add_argument("repo")
    df.add_argument("base", help="base commit (any git revision)")
    df.add_argument("head", help="head commit (any git revision)")
    df.add_argument("-o", "--out", required=True)
    df.add_argument("--budget", type=int, default=34)
    df.add_argument("--pr", type=int, help="pull request number on the GitHub remote, for links to its files view")
    df.add_argument("--title", help="what the change is called, shown in the viewer's header")
    df.add_argument("--description", help="a file with the PR's description, for review.md (default: pr.md in the output directory)")
    df.set_defaults(fn=cmd_diff)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
