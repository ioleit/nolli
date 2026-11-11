"""nolli command line.

    nolli build [<target>] [--repo DIR] [--data DIR] [--rebuild] [-o DIR] [--pr N] [--title T] [--description FILE]
    nolli view [<target>] [--repo DIR] [--data DIR] [--rebuild] [--host H] [--port N] [--no-open]
    nolli agent brief [<target>] | agent check <dataset> | agent skill [--install DIR]
    nolli serve [--cache DIR] [--keep SIZE] [--builds N] [--timeout S] [--max-repo SIZE] [--host H] [--port N]
    nolli extract <repo> -o <dir>                  (older shorthand: build WORKTREE --repo <repo> -o <dir>)
    nolli diff <repo> <base> <head> -o <dir> ...   (older shorthand: build <base>..<head> --repo <repo> -o <dir> ...)

build builds a target's dataset under --data (default: $NOLLI_DATA, else $XDG_DATA_HOME/nolli
or ~/.local/share/nolli), reusing one built before, and prints its directory; with -o it
builds it afresh into that directory instead, with what is made from it (below). view starts
the viewer's server on the data directory and the repository you are in, and opens the
target's page in the browser: /?t=<target>, which builds it the same way if it is not built
yet (nolli/server/serve.py). agent brief builds a change for a review agent.

<target>: what to build, in the repository you are in (or --repo DIR). nolli prints what it
resolved to before building.

A dataset already built:
  <dir>                a dataset directory (one with a key.json), as nolli agent brief
                       prints it: shown as it is, nothing is built

A snapshot (one model of the files at one point):
  R                    commit R as a whole: a branch, tag, sha, HEAD~2, ...
  WORKTREE             the files on disk now: HEAD plus uncommitted edits, staged or not,
                       and untracked files that are not ignored

A change (what changed between two snapshots):
  A..B                 exactly from A to B
  A...B                from where A and B diverged to B (what a pull request shows)
  ...B                 from where B diverged from the default branch to B: what B adds
                       ("...feature" is the change of branch feature)
  R^!                  commit R against its first parent (git's own notation)
  (nothing)            ...HEAD: your branch's committed change. On the default branch, or
                       with nothing new on the branch, an error that names the forms above
  A and B can be WORKTREE:
    HEAD..WORKTREE     your uncommitted edits
    ...WORKTREE        your branch plus uncommitted edits: the PR it would become

On GitHub (fetched into the repository you are in, objects only, or into a clone under the
data directory when you are in none):
  https://github.com/<o>/<r>                    the default branch's head, a snapshot
  https://github.com/<o>/<r>/tree/<ref>         <ref> as a snapshot
  https://github.com/<o>/<r>/commit/<sha>       <sha>^!, as GitHub shows a commit
  https://github.com/<o>/<r>/compare/<a>...<b>  <a>...<b> (also <a>..<b>)
  https://github.com/<o>/<r>/pull/<n>           the PR, with its title and description
  #<n>                                          PR <n> of this repository's GitHub remote
                                                (quote it: # starts a shell comment)

nolli agent brief reviews a change, so a single revision R there means what R adds: ...R,
or R^! if R is already on the default branch. A snapshot (WORKTREE alone, a /tree/ URL, the
repository's URL) is refused there, as is a change with nothing in it.

The default branch: origin/HEAD if the clone has it, else main or master. A branch or tag
named WORKTREE is refused: rename it, or name it as refs/heads/WORKTREE. WORKTREE is kept in
the repository as a commit object of its own (no branch, ref or index changes): the same
files give the same commit, so a build of them is reused.

agent is for a review agent (the skill, nolli agent skill): brief builds the dataset and
prints where review.md (what to do) is and where verdicts.json goes (both in a directory of
the agent's own, <data>/.agent/<name>); check validates the verdicts against the diagram they
answer and, when they pass, makes them the dataset's review.

serve is the same server for GitHub repositories, for everyone who can reach it: a target on
GitHub is its URL with this host (/<owner>/<repo>/pull/<n> shows that PR), for every
repository its token can read (nolli/server/serve.py).

Under --data, a dataset is a directory of the store (nolli/store/, DirectoryStore): a commit's
key.json, model.json (its structure) and overlays/ (what each extractor said); a change's
key.json, change.json, base/ and head/ (its commits) and its own overlays/ (git's hunks, a
review's verdicts, which the viewer shows). build -o writes one to a directory of your
choice, with what is made from it too: report.json (counts, unresolved references, extractor
notes, nolli/logic/formats/report.py); for a change also changes.json (the numbered changes),
review.mmd (Mermaid with every change in source code unfolded) and review.md (what a review
agent gets: that diagram, how to answer, and the change's description, from --description).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .logic.sources.git import ChangeError
from .model.keys import ChangeKey
from .progress import Progress
from .store import DirectoryStore


def find_data_dir(arg: str | None) -> Path:
    """Where built datasets are kept: --data, $NOLLI_DATA, $XDG_DATA_HOME/nolli or ~/.local/share/nolli."""
    import os

    if arg or os.environ.get("NOLLI_DATA"):
        return Path(arg or os.environ["NOLLI_DATA"]).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share").expanduser() / "nolli"


class Built:
    """A target, built or found in the store: what build, view and the agent commands work on."""

    def __init__(self, asked, store: DirectoryStore):
        from .datasets import Datasets

        self.asked, self.store, self.key = asked, store, asked.key
        self.repo, self.spec = asked.repo, asked.spec
        self.datasets = Datasets(store)
        self.out = asked.dataset or store.locate(asked.key)
        self.name = self.out.name


def resolve(args: argparse.Namespace, cmd: str, review: bool = False):
    """args.target resolved in args.repo (``datasets.resolve_target``); None, after saying why,
    if it cannot be. Says what it resolved to on stderr."""
    from .datasets import BUILD_ERRORS, resolve_target

    try:
        asked = resolve_target(args.target, Path(args.repo), find_data_dir(args.data) / ".repos", review=review)
    except BUILD_ERRORS as exc:
        print(f"nolli {cmd}: {exc}", file=sys.stderr)
        return None
    spec = asked.spec
    if spec is not None:
        what = f"{spec.base[:7]}..{spec.head[:7]} (a change)" if spec.is_diff else f"{spec.head[:7]} (a snapshot)"
        print(f"{asked.key.repo.format_name()}: {what}  {spec.title}", file=sys.stderr)
    return asked


def build(args: argparse.Namespace, cmd: str, review: bool = False) -> Built | None:
    """Resolve args.target and build it into the data directory unless a fresh one is stored
    there (or it is a dataset directory already). Progress goes to stderr; None (after saying
    why) if the target cannot be resolved or built."""
    from .datasets import BUILD_ERRORS, Datasets

    asked = resolve(args, cmd, review)
    if asked is None:
        return None
    if asked.dataset is not None:
        return Built(asked, DirectoryStore(asked.dataset.parent))
    store = DirectoryStore(find_data_dir(args.data))
    datasets = Datasets(store, asked.sources)
    b = Built(asked, store)
    if args.rebuild or not datasets.is_built(asked.key):
        print(f"building {b.out}", file=sys.stderr, flush=True)
        t0 = time.time()
        about = {**asked.about, **find_about(args), "budget": args.budget}
        try:
            datasets.build(asked.key, rebuild=args.rebuild, progress=Progress(), about=about)
            key = asked.key
            m = store.load_model(key.list_sides()[1] if isinstance(key, ChangeKey) else key)
        except BUILD_ERRORS as exc:
            print(f"nolli {cmd}: {exc}", file=sys.stderr)
            return None
        print(f"built in {time.time() - t0:.1f} s: {count_found(m)}", file=sys.stderr)
    return b


def find_about(args: argparse.Namespace) -> dict:
    """What --pr, --title and --description say about a change (build, diff), over what its
    target says."""
    desc = getattr(args, "description", None)
    return {k: v for k, v in {"pr": getattr(args, "pr", None), "title": getattr(args, "title", None),
                              "description": Path(desc).read_text() if desc else None}.items() if v}


def count_found(m) -> str:
    """What a build found, in a few words."""
    kinds = m.count_nodes_by_kind()
    parts = [f"{m.meta.get('files') or kinds.get('file', 0)} files"]
    parts += [f"{kinds[k]} {k}{'es' if k == 'class' else 's'}" for k in ("class", "interface", "type", "enum", "service") if kinds.get(k)]
    parts.append(f"{len(m.list_edges())} dependencies")
    return ", ".join(parts)


def cmd_build(args: argparse.Namespace) -> int:
    """nolli build: the target's dataset in the data directory (or afresh under -o); prints its directory."""
    if args.out:
        written = build_into(args, "build")
        if written is None:
            return 2
        print(Path(args.out).resolve())
        return 0
    b = build(args, "build")
    if b is None:
        return 2
    print(b.out)
    return 0


def build_into(args: argparse.Namespace, cmd: str) -> tuple[dict, dict] | None:
    """The target built afresh into the directory -o with its extras (``datasets.write_dataset``):
    what it says about itself and the report; None after saying why it could not be."""
    from .datasets import BUILD_ERRORS, write_dataset

    asked = resolve(args, cmd)
    if asked is None:
        return None
    if asked.dataset is not None:
        print(f"nolli {cmd}: {args.target} is built already; -o builds from commits", file=sys.stderr)
        return None
    try:
        return write_dataset(asked, Path(args.out), {**asked.about, **find_about(args), "budget": args.budget}, args.budget)
    except BUILD_ERRORS as exc:
        print(f"nolli {cmd}: {exc}", file=sys.stderr)
        return None


def cmd_view(args: argparse.Namespace) -> int:
    """nolli view: a server on the data directory and the repository you are in, opened on the
    target's page, which builds it if it is not built yet."""
    import webbrowser
    from urllib.parse import quote

    from .datasets import parse_target
    from .logic.sources.git import find_toplevel
    from .server.serve import make_local_server

    if args.build_only:  # before nolli build
        return cmd_build(argparse.Namespace(**{**vars(args), "out": None}))
    if args.rebuild and build(args, "view") is None:
        return 2
    data = find_data_dir(args.data)
    try:
        local = find_toplevel(Path(args.repo).resolve())
    except ChangeError:
        local = None
    try:
        target = parse_target(args.target, local)
    except ChangeError as exc:
        print(f"nolli view: {exc}", file=sys.stderr)
        return 2
    if target.kind == "dataset":  # served from the store it is in
        data = target.path.parent
    try:
        srv = make_local_server(data, local, args.host, args.port, args.budget)
    except OSError:  # the port is taken (another viewer, perhaps): any free one
        srv = make_local_server(data, local, args.host, 0, args.budget)
    url = f"http://{args.host}:{srv.server_address[1]}/?t={quote(args.target or '', safe='')}"
    print(f"viewer: {url}  (Ctrl-C to stop)", file=sys.stderr, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """nolli extract <repo> -o <dir>: build WORKTREE in <repo> afresh into <dir> (a directory
    that is no git repository: its files); prints a summary of the report."""
    from .datasets import extract_directory
    from .logic.sources.git import WORKTREE, find_head

    repo = Path(args.repo).resolve()
    if find_head(repo) is None and not (repo / ".git").exists():
        report = extract_directory(repo, Path(args.out), args.budget)
    else:
        written = build_into(argparse.Namespace(target=WORKTREE, repo=str(repo), data=None, out=args.out, budget=args.budget), "extract")
        if written is None:
            return 2
        report = written[1]
    print(json.dumps({"entities": report["summary"]["entities"], "relations": report["summary"]["relations"],
                      "view": {"visible": report["view"]["visible"], "edges": report["view"]["edges"]},
                      "unresolved": len(report["unresolved"])}, indent=1))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """nolli diff <repo> <base> <head> -o <dir>: build <base>..<head> in <repo> afresh into <dir>
    (the description from --description, else pr.md in <dir>); prints a summary."""
    out = Path(args.out)
    desc = args.description or (str(out / "pr.md") if (out / "pr.md").is_file() else None)
    written = build_into(argparse.Namespace(**{**vars(args), "target": f"{args.base}..{args.head}", "data": None, "description": desc}), "diff")
    if written is None:
        return 2
    about, report = written
    print(json.dumps({"diff": {k: v for k, v in about.items() if k not in ("description",)} | {"base": about["base"][:7], "head": about["head"][:7]},
                      "changes": report.get("diff"), "view": {"visible": report["view"]["visible"], "edges": report["view"]["edges"]}},
                     indent=1))
    return 0


class Gone(argparse.Action):
    """An option nolli no longer has: says what to type instead."""

    def __init__(self, option_strings, dest, instead: str = "", **kw):
        super().__init__(option_strings, dest, nargs=0, help=argparse.SUPPRESS, **kw)
        self.instead = instead

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(f"{option_string} is gone: {self.instead}")


def cmd_serve(args: argparse.Namespace) -> int:
    from .server.serve import main as serve

    argv = ["--host", args.host, "--budget", str(args.budget)]
    argv += ["--cache", args.cache] if args.cache else []
    argv += ["--port", str(args.port)] if args.port else []
    argv += ["--keep", args.keep] if args.keep else []
    for flag, value in (("--builds", args.builds), ("--timeout", args.timeout), ("--max-repo", args.max_repo)):
        argv += [flag, str(value)] if value is not None else []
    return serve(argv)


def find_agent_dir(store: DirectoryStore, name: str) -> Path:
    """Where a review agent run from the command line works on the model ``name``: the brief it
    reads, the verdicts it writes (beside the store, not in it)."""
    return store.root / ".agent" / name


def cmd_brief(args: argparse.Namespace) -> int:
    """nolli agent brief: build the change and say, as JSON on stdout, where the brief is, where
    the verdicts go and how to check them."""

    b = build(args, "agent brief", review=True)
    if b is None:
        return 2
    spec = b.spec
    work = find_agent_dir(b.store, b.name)
    work.mkdir(parents=True, exist_ok=True)
    (work / "review.md").write_text(b.datasets.make_review_brief(b.datasets.open_scene(b.key)))
    verdicts = work / "verdicts.json"
    print(json.dumps({
        "repository": str(b.repo), "base": spec.base, "head": spec.head, "title": spec.title, "dataset": str(b.out),
        "brief": str(work / "review.md"), "verdicts": str(verdicts),
        "has_verdicts": verdicts.is_file() or b.datasets.find_provenance(b.key) is not None,
        "inspect": f"git -C {b.repo} diff {spec.base[:12]} {spec.head[:12]}",
        "check": f"nolli agent check {b.out}",
        "view": f"nolli view {b.out}",
    }, indent=1))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """nolli agent check: the verdicts against the diagram they answer; problems on stderr. Verdicts
    that pass become the change's review."""
    from .datasets import VERDICTS, Datasets

    target = Path(args.dataset).expanduser().resolve()
    vf: Path | None = None
    if target.is_file():  # a verdicts file: the agent's (.agent/<name>/verdicts.json), or one in the dataset's directory
        vf = target
        target = target.parent.parent.parent / target.parent.name if target.parent.parent.name == ".agent" else target.parent
    ds = target if (target / "key.json").is_file() else find_data_dir(args.data) / args.dataset
    store = DirectoryStore(ds.parent)
    datasets = Datasets(store)
    key = store.find_key(ds.name)
    scene = datasets.open_scene(key) if isinstance(key, ChangeKey) else None
    if scene is None:
        print(f"nolli agent check: {args.dataset} is not a change's dataset", file=sys.stderr)
        return 2
    if vf is None:
        vf = find_agent_dir(store, ds.name) / "verdicts.json"
        if not vf.is_file() and (ds / "verdicts.json").is_file():  # written into the dataset's directory by hand
            vf = ds / "verdicts.json"
    if not vf.is_file():
        print(f"nolli agent check: no {vf}", file=sys.stderr)
        return 1
    verdicts, errors = datasets.check_verdicts(scene, vf.read_text())
    for e in errors:
        print(e, file=sys.stderr)
    counts = {v: sum(x["verdict"] == v for x in verdicts) for v in reversed(VERDICTS)}
    print(json.dumps({"ok": not errors, "verdicts": len(verdicts), **counts, "errors": len(errors)}))
    if errors:
        return 1
    datasets.keep_verdicts(key, verdicts, {"by": "agent"})
    return 0


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nolli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    target_help = "what to build (see nolli --help): a dataset directory, R, WORKTREE, A..B, A...B, ...B, R^!, a GitHub URL or '#<n>' (default: ...HEAD)"

    def add_target(p: argparse.ArgumentParser, help: str = target_help) -> None:
        p.add_argument("target", nargs="?", help=help)
        p.add_argument("--repo", default=".", help="the repository (default: the one you are in)")
        p.add_argument("--data", help="where datasets are kept (default: $NOLLI_DATA, else $XDG_DATA_HOME/nolli or ~/.local/share/nolli)")
        p.add_argument("--budget", type=int, default=34)
        p.add_argument("--rebuild", action="store_true", help="build the dataset again even if it exists")
        p.add_argument("--whole", action=Gone, instead="name a snapshot as the target: R (HEAD for the current one), WORKTREE, "
                       "or a repository's GitHub URL")

    bd = sub.add_parser("build", help="build a target's dataset (a snapshot or a change) and print its directory")
    add_target(bd)
    bd.add_argument("-o", "--out", help="build afresh into this directory, with its report and review files, not into --data")
    bd.add_argument("--pr", type=int, help="pull request number on the GitHub remote, for links to its files view")
    bd.add_argument("--title", help="what the change is called, shown in the viewer's header")
    bd.add_argument("--description", help="a file with the change's description, for review.md")
    bd.set_defaults(fn=cmd_build)
    vw = sub.add_parser("view", help="show a target in the viewer, building it (as nolli build) if it is not built yet")
    add_target(vw)
    vw.add_argument("--build-only", action="store_true", help=argparse.SUPPRESS)  # before nolli build
    vw.add_argument("--host", default="127.0.0.1")
    vw.add_argument("--port", type=int, default=8720, help="default 8720; a free one if that is taken")
    vw.add_argument("--no-open", action="store_true", help="don't open the browser")
    vw.set_defaults(fn=cmd_view)
    ag = sub.add_parser("agent", help="for a review agent: the brief of a change, checking its verdicts, the skill")
    agsub = ag.add_subparsers(dest="agent_cmd", required=True)
    br = agsub.add_parser("brief", help="build a change's dataset and print (JSON) where its brief and verdicts are")
    add_target(br, "a change, as for nolli build; a single revision R is what R adds (...R, or R^! if R is on the default branch)")
    br.set_defaults(fn=cmd_brief)
    ck = agsub.add_parser("check", help="check a change's verdicts.json against its diagram")
    ck.add_argument("dataset", help="the dataset directory (or its name under --data), or a verdicts file in it")
    ck.add_argument("--data")
    ck.set_defaults(fn=cmd_check)
    sk = agsub.add_parser("skill", help="print the review skill (SKILL.md), or install it")
    sk.add_argument("--install", metavar="DIR", help="a skills directory, e.g. ~/.claude/skills or .claude/skills; "
                    "writes DIR/nolli-review/SKILL.md")
    sk.set_defaults(fn=cmd_skill)
    sv = sub.add_parser("serve", help="the viewer as a service: GitHub's paths (/<owner>/<repo>/pull/<n>) on this host, "
                        "for every repository $NOLLI_GITHUB_TOKEN can read (without it, public ones), "
                        "to everyone who can reach the host")
    sv.add_argument("--cache", help="clones and datasets (default: $NOLLI_CACHE, else ~/.cache/nolli-serve)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, help="default: $PORT, else 8730")
    sv.add_argument("--budget", type=int, default=34)
    sv.add_argument("--keep", help="the cache's size at most, e.g. 20G: what was used longest ago goes first "
                    "(default: $NOLLI_CACHE_KEEP, else keep everything)")
    sv.add_argument("--builds", type=int, help="builds at the same time, each its own process (default: $NOLLI_BUILDS, else the CPUs, at most 4)")
    sv.add_argument("--timeout", type=float, help="seconds a build may take before it is stopped (default: $NOLLI_BUILD_TIMEOUT, else 600)")
    sv.add_argument("--max-repo", help="the largest repository built, by GitHub's size of it, e.g. 500M; 0 for no limit "
                    "(default: $NOLLI_MAX_REPO, else 1G)")
    sv.set_defaults(fn=cmd_serve)
    ex = sub.add_parser("extract", help="older shorthand: nolli build WORKTREE --repo <repo> -o <dir>")
    ex.add_argument("repo")
    ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--budget", type=int, default=34)
    ex.set_defaults(fn=cmd_extract)
    df = sub.add_parser("diff", help="older shorthand: nolli build <base>..<head> --repo <repo> -o <dir>")
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
