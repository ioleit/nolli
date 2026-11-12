"""nolli command line.

    nolli build [<target>] [--repo DIR] [--data DIR] [--rebuild] [-o DIR] [--pr N] [--title T] [--description FILE]
    nolli view [<target>] [--repo DIR] [--data DIR] [--rebuild] [--host H] [--port N] [--no-open]
    nolli agent brief [<target>] | agent check <dataset> | agent skill [--install DIR]
    nolli serve [--cache DIR] [--keep SIZE] [--builds N] [--timeout S] [--max-repo SIZE] [--host H] [--port N]
    nolli extract <repo> -o <dir>                  (older shorthand: build WORKTREE --repo <repo> -o <dir>)
    nolli diff <repo> <base> <head> -o <dir> ...   (older shorthand: build <base>..<head> --repo <repo> -o <dir> ...)

build builds a target's model into the store under --data (default: $NOLLI_DATA, else
$XDG_DATA_HOME/nolli or ~/.local/share/nolli), reusing one built before, and prints its key;
with -o it also writes it to that directory as a dataset, with what is made from it (below).
view starts
the viewer's server on the data directory and the repository you are in, and opens the
target's page in the browser: /?t=<target>, which builds it the same way if it is not built
yet (nolli/server/serve.py). agent brief builds a change for a review agent.

<target>: what to build, in the repository you are in (or --repo DIR). nolli prints what it
resolved to before building.

A model already built:
  <key>                its key, as nolli build and nolli agent brief print it
                       (github.com/<o>/<r>@<sha>, …@<base>..<head>): nothing is built
  <dir>                a dataset directory (one with a key.json, as build -o writes it):
                       imported into --data as it is, nothing is built

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

agent is for a review agent (the skill, nolli agent skill): brief builds the change and
prints where review.md (what to do) is and where verdicts.json goes (both in a directory of
the agent's own under <data>/.agent/); check validates the verdicts against the diagram they
answer and, when they pass, makes them the change's review.

serve is the same server for GitHub repositories, for everyone who can reach it: a target on
GitHub is its URL with this host (/<owner>/<repo>/pull/<n> shows that PR), for every
repository its token can read (nolli/server/serve.py).

--data holds the store (nolli/store/: what is built, by repository, in a layout of its own
that nothing else reads), a review agent's files (.agent/) and clones (.repos/). Files go in
and out of it only as dataset directories (nolli/logic/formats/dataset.py): build -o writes one, and view
and agent check take one, importing it first. A dataset holds a commit's key.json,
model.json (its structure) and overlays/ (what each extractor said); for a change also
change.json, base/ and head/ (its commits) and its own overlays/ (git's hunks, a review's
verdicts, which the viewer shows). Beside them, what is made from the model: report.json
(counts, unresolved references, extractor notes, nolli/logic/formats/report.py); for a change
also changes.json (the numbered changes), review.mmd (Mermaid with every change in source
code unfolded) and review.md (what a review agent gets: that diagram, how to answer, and the
change's description, from --description).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nolli.logic.sources.git import ChangeError, GitError, find_toplevel
from .model.keys import ChangeKey
from .progress import Progress
from nolli.model.keys import ModelKey
from nolli.store import ModelStore, RepoStoreProvider


def find_data_dir(arg: str | None) -> Path:
    """Return where built models are kept (the store, a review agent's files, clones): --data,
    $NOLLI_DATA, $XDG_DATA_HOME/nolli or ~/.local/share/nolli."""
    import os

    if arg or os.environ.get("NOLLI_DATA"):
        return Path(arg or os.environ["NOLLI_DATA"]).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share").expanduser() / "nolli"


def open_stores(args: argparse.Namespace) -> RepoStoreProvider:
    """Return the provider of the stores under the data directory (--data)."""
    return RepoStoreProvider(find_data_dir(getattr(args, "data", None)))


class Built:
    """A model built, or imported, in the store of its repository: what build, view and the agent
    commands work on. ``target`` (pinned) and ``sources`` are what it was built from, None for a
    dataset directory imported as it is."""

    def __init__(self, store: ModelStore, key: ModelKey, target=None, sources=None):
        self.store, self.key, self.target, self.sources = store, key, target, sources


def find_local(repo: str) -> Path | None:
    """Return the git repository around ``repo``, or None if it is in none."""
    try:
        return find_toplevel(Path(repo).resolve())
    except ChangeError:
        return None


def make_sources(args: argparse.Namespace):
    """Return where a build gets commits: the repository you are in, and clones under the data
    directory."""
    from nolli.logic.sources import github

    return github.Checkout(find_local(getattr(args, "repo", ".")), find_data_dir(getattr(args, "data", None)) / ".repos")


def import_named(text: str | None, cmd: str, stores: RepoStoreProvider):
    """Import the dataset directory ``text`` names (``import_dataset``) into its repository's
    store and return the store and the key, to use as it is. Return None if ``text`` names no
    dataset directory, and False, after printing why, for one this nolli cannot read."""
    from nolli.logic.formats import dataset

    if not text or not dataset.is_dataset(Path(text).expanduser()):
        return None
    d = Path(text).expanduser().resolve()
    try:
        store = stores.open(dataset.read_key(d).repo)
        return store, import_dataset(store, d)
    except dataset.DatasetError as exc:
        print(f"nolli {cmd}: {exc}", file=sys.stderr)
        return False


def pin(args: argparse.Namespace, cmd: str, review: bool = False):
    """Pin args.target in args.repo and return the pinned ``operations.Target``, or the (store, key) of
    the dataset directory it names, imported. With ``review`` (the agent commands), a single
    revision means what it adds (``Target.pin``'s ``adds``), and a snapshot or a dataset is
    refused. Print what it names on stderr; print why and return None if it names nothing."""
    from nolli import operations

    found = None if review else import_named(args.target, cmd, open_stores(args))
    if found is False:
        return None
    if found:
        return found
    if review and args.target and (Path(args.target) / "key.json").is_file():
        print(f"nolli {cmd}: {args.target} is a dataset built already; name the change to review, "
              f"or check verdicts: nolli agent check {args.target}", file=sys.stderr)
        return None
    local = find_local(args.repo)
    try:
        target = operations.Target.from_string(args.target, local)
        if target.kind == "revs" and local is None:
            raise ChangeError(f"{Path(args.repo)} is not in a git repository")
        target = target.pin(adds=review)
        if review and not target.is_change:
            raise ChangeError(f"{args.target or 'HEAD'} is a snapshot, not a change: a review needs a change "
                              f"(a branch, <base>...<head>, <commit>^! or a pull request)")
    except GitError as exc:  # ChangeError, FetchError: what the user named, or commits not to be had
        print(f"nolli {cmd}: {exc}", file=sys.stderr)
        return None
    if target.kind == "pinned":
        repo, what = target.repo, f"{target.base[:7]}...{target.head[:7]} (a change)"
    else:
        k = target.key
        repo = k.repo
        what = f"{k.base[:7]}..{k.head[:7]} (a change)" if isinstance(k, ChangeKey) else f"{k.sha[:7]} (a snapshot)"
    print(f"{repo.format_name()}: {what}  {target.about.get('title') or ''}", file=sys.stderr)
    return target


def build(args: argparse.Namespace, cmd: str, review: bool = False) -> Built | None:
    """Build args.target into its repository's store under the data directory unless a fresh one is
    kept there (or import the dataset directory it names), and return it as ``Built``. Progress goes
    to stderr; print why and return None if the target names nothing or cannot be built."""
    from nolli import operations

    target = pin(args, cmd, review)
    if target is None:
        return None
    if isinstance(target, tuple):  # a dataset directory, imported
        return Built(*target)
    store = open_stores(args).open(target.repo if target.kind == "pinned" else target.key.repo)
    sources = make_sources(args)
    try:
        key = operations.build_target(store, target, sources, about={**find_about(args), "budget": args.budget},
                          progress=Progress(), rebuild=getattr(args, "rebuild", False))
    except GitError as exc:  # ChangeError, FetchError: what the user named, or commits not to be had
        print(f"nolli {cmd}: {exc}", file=sys.stderr)
        return None
    m = store.load_model(key.list_sides()[1] if isinstance(key, ChangeKey) else key)
    print(f"{key.format_key()}: {count_found(m)}", file=sys.stderr)
    return Built(store, key, target, sources)


def find_about(args: argparse.Namespace) -> dict:
    """Collect what --pr, --title and --description say about a change (for build and diff) into
    a dict, leaving out the ones not given; these take precedence over what the target says."""
    desc = getattr(args, "description", None)
    return {k: v for k, v in {"pr": getattr(args, "pr", None), "title": getattr(args, "title", None),
                              "description": Path(desc).read_text() if desc else None}.items() if v}


def count_found(m) -> str:
    """Summarise what a build found in a few words, e.g. "12 files, 3 classes, 40 dependencies"."""
    kinds = m.count_nodes_by_kind()
    parts = [f"{m.meta.get('files') or kinds.get('file', 0)} files"]
    parts += [f"{kinds[k]} {k}{'es' if k == 'class' else 's'}" for k in ("class", "interface", "type", "enum", "service") if kinds.get(k)]
    parts.append(f"{len(m.list_edges())} dependencies")
    return ", ".join(parts)


def cmd_build(args: argparse.Namespace) -> int:
    """nolli build: build the target into the data directory and print its key; with -o, also
    write it to that directory as a dataset, and print the directory."""
    if args.out:
        if build_into(args, "build") is None:
            return 2
        print(Path(args.out).resolve())
        return 0
    b = build(args, "build")
    if b is None:
        return 2
    print(b.key.format_key())
    return 0


def build_into(args: argparse.Namespace, cmd: str) -> tuple[dict, dict] | None:
    """Build the target (as ``build``), then export it to the directory -o names as a dataset with
    what is made from it (``write_extras``). Return what it says about itself and its
    report; print why and return None if it cannot be built."""
    b = build(args, cmd)
    return None if b is None else export_into(b.store, b.key, Path(args.out), args.budget)


def export_into(store: ModelStore, key: ModelKey, out: Path, budget: int | None) -> tuple[dict, dict]:
    """Export the model of ``key`` to the dataset directory ``out`` with what is made from it, and
    return what it says about itself (a change's record, else nothing) and its report."""
    out.mkdir(parents=True, exist_ok=True)
    export_dataset(store, key, out)
    report = write_extras(store, key, out, budget)
    return (store.load_change(key) if isinstance(key, ChangeKey) else None) or {}, report


# -- dataset directories in and out of a store (nolli/logic/formats/dataset.py) -------------


def _read_dataset(store: ModelStore, key: ModelKey):
    """Return the model ``store`` keeps under ``key`` as a ``Dataset``, every map with its
    ``about``: for a change, what it says about itself, its merged model if one is kept and its
    two commits. Raise KeyError if the store does not have it."""
    from nolli.logic.formats.dataset import Dataset

    text = key.format_key()
    maps = {n: (dict(store.open_maps(n).load(text)), store.open_maps(n).load_about(text)) for n in store.list_map_names(text)}
    if isinstance(key, ChangeKey):
        about = store.load_change(key)
        if about is None:
            raise KeyError(f"no change {text} to export")
        sides = {side: _read_dataset(store, c) for side, c in zip(("base", "head"), key.list_sides(), strict=True)}
        return Dataset(key, store.load_model(key), maps, about, sides)
    model = store.load_model(key)
    if model is None:
        raise KeyError(f"no model {text} to export")
    return Dataset(key, model, maps)


def export_dataset(store: ModelStore, key: ModelKey, out: Path) -> None:
    """Write the model ``store`` keeps under ``key`` to the dataset directory ``out``, in place of
    a model written there before. Raise KeyError if the store does not have it."""
    from nolli.logic.formats import dataset

    dataset.write_dataset(_read_dataset(store, key), out)


def import_dataset(store: ModelStore, d: Path) -> ModelKey:
    """Keep the model of the dataset directory ``d`` in ``store``, in place of what the store kept
    under its key, and return the key. Maps that say where they came from (``about``, a review's
    verdicts) are kept as they say it; the others go in with the model, as extraction's do.
    Raise DatasetError for a directory that is no dataset of this version, and NotAllowed if the
    store does not keep models of its repository."""
    from nolli.logic.formats import dataset

    ds = dataset.read_dataset(d)

    def plain(x) -> dict:
        return {n: values for n, (values, about) in x.maps.items() if about is None}

    if isinstance(ds.key, ChangeKey):
        store.save_change(ds.key, ds.change or {}, {s: (x.model, plain(x)) for s, x in ds.sides.items()}, plain(ds), ds.model)
    else:
        store.save_model(ds.key, ds.model, plain(ds))  # type: ignore[arg-type]  # a commit's dataset has a model
    for x in (ds, *ds.sides.values()):
        for name, (values, about) in x.maps.items():
            if about is not None:
                store.open_maps(name).save(x.key.format_key(), values, about)
    return ds.key


def write_extras(store: ModelStore, key: ModelKey, out: Path, budget: int | None = None) -> dict:
    """Write the files a dataset directory gets beside the model: report.json, and for a change what
    a review agent reads (changes.json, review.mmd, review.md). ``budget`` is the size of the
    default projection when the model does not say it. Extras written there before go. Return
    the report."""
    from nolli import operations
    from nolli.logic import transformations
    from nolli.logic.formats import dataset
    from nolli.logic.formats import report as report_format
    from nolli.logic.transformations import merge, projection
    from nolli.logic.transformations.merge import items
    from nolli.review import brief

    scene = operations.open_scene(store, key)
    if scene is None:
        raise KeyError(f"no model {key.format_key()} to describe")
    meta = scene.model.meta
    budget = (meta.get("change") or {}).get("budget") or meta.get("budget") or budget or projection.DEFAULT_BUDGET
    default = transformations.apply(scene, projection.fold(projection.ProjectionSpec(expand=projection.AtMost(budget))))
    report = report_format.make_report(scene, default)
    out = Path(out)
    dataset.remove(out, dataset.EXTRAS)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    if merge.is_change(scene):
        (out / "changes.json").write_text(json.dumps(items.list_changes(scene), indent=1))
        mmd = brief.make_review_diagram(scene)
        (out / "review.mmd").write_text(mmd)
        (out / "review.md").write_text(brief.make_brief(scene, mmd))
    return report


def cmd_view(args: argparse.Namespace) -> int:
    """nolli view: a server on the data directory and the repository you are in, opened on the
    target's page, which builds it if it is not built yet."""
    import webbrowser
    from urllib.parse import quote

    from nolli.operations import Target
    from .server.serve import make_local_server

    if args.build_only:  # before nolli build
        return cmd_build(argparse.Namespace(**{**vars(args), "out": None}))
    if args.rebuild and build(args, "view") is None:
        return 2
    data = find_data_dir(args.data)
    local = find_local(args.repo)
    text = args.target
    found = import_named(text, "view", RepoStoreProvider(data))
    if found is False:
        return 2
    if found:  # a dataset directory, imported into the data directory: its key is the target
        text = found[1].format_key()
    else:
        try:
            Target.from_string(text, local)
        except ChangeError as exc:
            print(f"nolli view: {exc}", file=sys.stderr)
            return 2
    try:
        srv = make_local_server(data, local, args.host, args.port, args.budget)
    except OSError:  # the port is taken (another viewer, perhaps): any free one
        srv = make_local_server(data, local, args.host, 0, args.budget)
    url = f"http://{args.host}:{srv.server_address[1]}/?t={quote(text or '', safe='')}"
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
    """nolli extract <repo> -o <dir>: build WORKTREE in <repo> (a directory that is no git
    repository: its files, built every time) and write it to <dir> as a dataset; prints a summary
    of the report."""
    from nolli import operations
    from nolli.model.keys import Repo
    from .logic.sources.git import WORKTREE, find_head

    repo = Path(args.repo).resolve()
    if find_head(repo) is None and not (repo / ".git").exists():  # no git repository: its files as they are
        key = Repo(str(repo)).make_commit("worktree")
        store = open_stores(args).open(key.repo)
        operations.build_files(store, repo, key)
        operations.precompute_scene(store, key)
        report = export_into(store, key, Path(args.out), args.budget)[1]
    else:
        written = build_into(argparse.Namespace(target=WORKTREE, repo=str(repo), data=getattr(args, "data", None), out=args.out,
                                                budget=args.budget), "extract")
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


def find_agent_dir(args: argparse.Namespace, key: ModelKey) -> Path:
    """Return where a review agent run from the command line works on the model of ``key``: the
    brief it reads and the verdicts it writes, under the data directory beside the store (not in
    it), with the key in a file ``key`` so that ``agent check`` given the verdicts file knows the model."""
    import re

    d = find_data_dir(getattr(args, "data", None)) / ".agent" / re.sub(r"[^A-Za-z0-9._-]+", "_", key.format_key())
    d.mkdir(parents=True, exist_ok=True)
    (d / "key").write_text(key.format_key() + "\n")
    return d


def format_command(args: argparse.Namespace, *words: str) -> str:
    """Return the nolli command line ``words`` with this run's --data, quoted for a shell."""
    import shlex

    data = ["--data", str(find_data_dir(args.data).resolve())] if getattr(args, "data", None) else []
    return shlex.join(["nolli", *words, *data])


def cmd_brief(args: argparse.Namespace) -> int:
    """nolli agent brief: build the change and say, as JSON on stdout, where the brief is, where
    the verdicts go and how to check them."""

    from nolli import operations
    from nolli.review import brief, verdicts

    b = build(args, "agent brief", review=True)
    if b is None:
        return 2
    key, title = b.key, b.target.about.get("title") or ""
    with b.sources.open_commits(key.repo, key.base, key.head) as repo:  # yours, or a clone: where the agent reads the change
        pass
    work = find_agent_dir(args, key)
    (work / "review.md").write_text(brief.make_brief(operations.open_scene(b.store, b.key)))
    answer = work / "verdicts.json"
    print(json.dumps({
        "repository": str(repo), "base": key.base, "head": key.head, "title": title, "key": key.format_key(),
        "brief": str(work / "review.md"), "verdicts": str(answer),
        "has_verdicts": answer.is_file() or verdicts.find_provenance(b.store, b.key) is not None,
        "inspect": f"git -C {repo} diff {key.base[:12]} {key.head[:12]}",
        "check": format_command(args, "agent", "check", key.format_key()),
        "view": format_command(args, "view", key.format_key()),
    }, indent=1))
    return 0


def find_checked(args: argparse.Namespace) -> tuple[ModelStore, ModelKey, Path | None, Path | None] | None:
    """Work out what ``agent check`` checks from ``args.dataset``: a change's key, a dataset
    directory (imported), the agent's work directory (``find_agent_dir``) or a verdicts file in
    either. Return the change's store and key, the verdicts file named (or None) and the dataset
    directory named (or None); print why and return None if it names no change."""
    from nolli.logic.formats import dataset

    stores = open_stores(args)
    p = Path(args.dataset).expanduser()
    vf = p.resolve() if p.is_file() else None
    d = vf.parent if vf else p.resolve()
    if dataset.is_dataset(d):
        found = import_named(str(d), "agent check", stores)
        if not found:
            return None
        return found[0], found[1], vf, d
    if (d / "key").is_file() and d.parent.name == ".agent":  # an agent's directory: in the data directory it was made in
        key = ModelKey.parse((d / "key").read_text().strip())
        stores = stores if getattr(args, "data", None) else RepoStoreProvider(d.parent.parent)
    else:
        key = ModelKey.parse(args.dataset)
    if not isinstance(key, ChangeKey):
        print(f"nolli agent check: {args.dataset} names no change: give its key, its dataset directory or its verdicts file",
              file=sys.stderr)
        return None
    return stores.open(key.repo), key, vf, None


def cmd_check(args: argparse.Namespace) -> int:
    """nolli agent check: the verdicts against the diagram they answer; problems on stderr. Verdicts
    that pass become the change's review, in the data directory's store."""
    from nolli import operations

    from nolli.review import brief, verdicts

    found = find_checked(args)
    if found is None:
        return 2
    store, key, vf, ds = found
    scene = operations.open_scene(store, key) if isinstance(key, ChangeKey) else None
    if scene is None:
        print(f"nolli agent check: {args.dataset} is not a change built here (nolli agent brief builds it)", file=sys.stderr)
        return 2
    if vf is None:
        vf = find_agent_dir(args, key) / "verdicts.json"
        if not vf.is_file() and ds is not None and (ds / "verdicts.json").is_file():  # written into the dataset's directory by hand
            vf = ds / "verdicts.json"
    if not vf.is_file():
        print(f"nolli agent check: no {vf}", file=sys.stderr)
        return 1
    kept, errors = verdicts.check_verdicts(scene, brief.make_review_diagram(scene), vf.read_text())
    for e in errors:
        print(e, file=sys.stderr)
    counts = {v: sum(x["verdict"] == v for x in kept) for v in reversed(verdicts.VERDICTS)}
    print(json.dumps({"ok": not errors, "verdicts": len(kept), **counts, "errors": len(errors)}))
    if errors:
        return 1
    verdicts.save_verdicts(store, key, kept, {"by": "agent"})
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
    target_help = "what to build (see nolli --help): a model's key, a dataset directory, R, WORKTREE, A..B, A...B, ...B, R^!, a GitHub URL or '#<n>' (default: ...HEAD)"

    def add_target(p: argparse.ArgumentParser, help: str = target_help) -> None:
        p.add_argument("target", nargs="?", help=help)
        p.add_argument("--repo", default=".", help="the repository (default: the one you are in)")
        p.add_argument("--data", help="where built models are kept (default: $NOLLI_DATA, else $XDG_DATA_HOME/nolli or ~/.local/share/nolli)")
        p.add_argument("--budget", type=int, default=34)
        p.add_argument("--rebuild", action="store_true", help="build the model again even if it is built")
        p.add_argument("--whole", action=Gone, instead="name a snapshot as the target: R (HEAD for the current one), WORKTREE, "
                       "or a repository's GitHub URL")

    bd = sub.add_parser("build", help="build a target's model (a snapshot or a change) and print its key")
    add_target(bd)
    bd.add_argument("-o", "--out", help="also write it to this directory as a dataset, with its report and review files")
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
    ck.add_argument("dataset", help="the change: its key (as nolli agent brief prints the command), a dataset directory, "
                    "or a verdicts file in either's directory")
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
