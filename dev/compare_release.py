"""Times /api/scene of this checkout against a nolli release from PyPI, or against a git ref;
with --compare, lists every field in which the two versions' answers differ.

    python dev/compare_release.py [--release 0.7.0 | --ref origin/main] [--work DIR] [--runs 5]
                                  [--diff REPO BASE HEAD ...] [--compare]
                                  [repo URL or path ...]

Each version extracts the same repositories with its own ``nolli extract`` and serves them
with its own ``nolli view``; the same requests go to both: the presets, and unfolding
containers one at a time from the default projection, as the viewer asks when you click. The
table gives the median time of each request and whether both versions drew the same boxes.
The release (or the ref, exported with ``git archive``) is installed into a virtual
environment under the work directory. Extracted datasets are kept per version (the release
number, or the commit of the ref or of this checkout, with ``-dirty`` for uncommitted
changes, re-extracted every run), so a changed format is never read by the other version.

``--diff REPO BASE HEAD`` (repeatable) adds a change dataset, made by each version's own
``nolli diff``; the repository is cloned with its history. ``--compare`` asks both versions
the presets and Mermaid, and for a change also ``/api/changes`` and the brief, and prints each
field that differs: how often, and one example. The viewer JSON of format 1 (overlays under their names) is read back into the
older flat fields first (``projection.*``, ``label.text`` as ``label``, ``hover.text`` as
``info``, ``change`` as ``diff``, ``review.card`` as ``review``, ``items.items`` (``review.items``
until 0.9) as ``items``,
``verdicts.verdicts`` and ``verdicts.verdict`` as themselves), so a field that only moved is not a difference.

Prerequisites: git, network access for the release and the repositories, and this
checkout's ``nolli`` next to the Python that runs the script (``uv run python ...``).
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

REPOS = [
    "https://github.com/pallets/click",
    "https://github.com/pallets/flask",
    "https://github.com/ioleit/nolli",
    "https://github.com/fastapi/full-stack-fastapi-template",
]


def _run(*cmd: str | Path, cwd: Path | None = None, env: dict | None = None) -> None:
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True, stdout=subprocess.DEVNULL)


def _make_env(data: Path) -> dict:
    """Return the environment one version runs in: its own data directory ($NOLLI_DATA), where a
    version that builds into a store first (``-o``, ``view <dir>``) keeps what it builds. Versions
    before that ignore it."""
    return {**os.environ, "NOLLI_DATA": str(data)}


def _install_release(work: Path, version: str) -> Path:
    venv = work / f"release-{version}"
    if not (venv / "bin" / "nolli").exists():
        _run(sys.executable, "-m", "venv", venv)
        _run(venv / "bin" / "pip", "install", "-q", f"nolli-review=={version}")
    return venv / "bin" / "nolli"


def _install_ref(work: Path, ref: str) -> tuple[str, Path]:
    """Install the commit ``ref`` names (in this repository) into its own venv: (commit, nolli)."""
    root = Path(__file__).resolve().parent.parent
    commit = subprocess.run(["git", "rev-parse", "--short", f"{ref}^{{commit}}"], cwd=root, check=True,
                            capture_output=True, text=True).stdout.strip()
    tree, venv = work / f"tree-{commit}", work / f"ref-{commit}"
    if not tree.exists():  # an export, not a worktree, so it never shows up in this repository
        part = work / f"tree-{commit}.part"
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        archive = subprocess.run(["git", "archive", commit], cwd=root, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", part], input=archive, check=True)
        part.rename(tree)
    if not (venv / "bin" / "nolli").exists():
        _run(sys.executable, "-m", "venv", venv)
        _run(venv / "bin" / "pip", "install", "-q", tree)
    return commit, venv / "bin" / "nolli"


def _checkout_version() -> str:
    """Return this checkout's short commit, with ``-dirty`` when ``nolli/`` has uncommitted changes."""
    root = Path(__file__).resolve().parent.parent
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + ("-dirty" if git("status", "--porcelain", "--", "nolli") else "")


def _clone(work: Path, repo: str, history: bool = False) -> Path:
    """Return a local checkout of ``repo``: the path itself if it is a directory, else a clone kept
    under the work directory (shallow, or with its whole history for a change), cloned if missing."""
    if Path(repo).is_dir():
        return Path(repo).resolve()
    out = work / ("repos-full" if history else "repos") / repo.rstrip("/").rsplit("/", 1)[-1]
    if not out.exists():
        _run("git", "clone", "-q", *(["--filter=blob:none"] if history else ["--depth", "1"]), repo, out)
    return out


class Dataset:
    """A commit (``base`` None: the checkout as it is) or a change of one repository."""

    def __init__(self, src: Path, base: str | None = None, head: str | None = None) -> None:
        self.src, self.base, self.head = src, base, head
        self.name = src.name if base is None else f"{src.name}-{base.replace('/', '_')}..{head.replace('/', '_')}"

    @property
    def is_change(self) -> bool:
        return self.base is not None

    def build(self, nolli: Path, out: Path, cwd: Path, env: dict | None = None) -> None:
        if self.is_change:
            _run(nolli, "diff", self.src, self.base, self.head, "-o", out, cwd=cwd, env=env)
        else:
            _run(nolli, "extract", self.src, "-o", out, cwd=cwd, env=env)


def _read_key_text(dataset: Path) -> str | None:
    """Return the key a dataset directory's key.json names, as text, or None if it has none."""
    try:
        j = json.loads((dataset / "key.json").read_text())
    except (OSError, ValueError):
        return None
    if "base" in j:
        return f"{j['repo']}@{j['base']}..{j['head']}"
    return f"{j['repo']}@{j['head']}" if "repo" in j else None


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """One version's ``nolli view`` on one dataset directory."""

    def __init__(self, nolli: Path, dataset: Path, env: dict | None = None) -> None:
        self.nolli, self.port = nolli, _find_free_port()
        self.proc = subprocess.Popen([str(nolli), "view", str(dataset), "--no-open", "--port", str(self.port)],
                                     cwd=dataset.parent, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 60
        while True:
            try:
                self.names = self.find_name(dataset)
                return
            except OSError:
                if time.time() > deadline or self.proc.poll() is not None:
                    raise RuntimeError(f"{nolli} view {dataset} did not start") from None
                time.sleep(0.2)

    def find_name(self, dataset: Path) -> dict:
        """Find how this version's API names the dataset: by its key (``k``, which the page of
        ``/?t=<dataset's name>`` says; the server runs in the dataset's parent directory, so 0.9
        reads the name as a path and later versions find it in their store; a version that imports
        the dataset into a store of its own takes the key, ``/?t=<key>``) or, before 0.9, by its
        directory's name (``d``)."""
        up = self.answers("/")
        for target in (dataset.name, _read_key_text(dataset)):
            try:
                page = self.get("/?t=" + urllib.parse.quote(target, safe="")).decode() if target and up else ""
            except urllib.error.HTTPError:  # this version names it otherwise
                continue
            m = re.search(r'name="nolli-key" content="([^"]*)"', page)
            if m:
                return {"k": html.unescape(m.group(1))}
        names = [d["name"] for d in json.loads(self.get("/api/list"))]
        if dataset.name not in names:
            raise RuntimeError(f"{self.nolli} view serves {names}, not {dataset.name}")
        return {"d": dataset.name}

    def answers(self, path: str) -> bool:
        try:
            self.get(path)
            return True
        except urllib.error.HTTPError:
            return False

    def get(self, path: str) -> bytes:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=60) as r:
            return r.read()

    def ask(self, path: str, query: dict, as_json: bool = True):
        """Ask the server ``path?<the dataset>&query`` and return its answer, or {"error": ...} for an
        HTTP error or a timeout."""
        try:
            body = self.get(path + "?" + urllib.parse.urlencode({**self.names, **query}))
        except urllib.error.HTTPError as exc:
            return {"error": f"HTTP {exc.code}"}
        except TimeoutError:
            return {"error": "timed out"}
        return json.loads(body) if as_json else body.decode()

    def fetch_scene(self, query: dict) -> tuple[dict, float]:
        t0 = time.perf_counter()
        body = self.get("/api/scene?" + urllib.parse.urlencode({**self.names, **query}))
        return json.loads(body), (time.perf_counter() - t0) * 1000

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait()


def _list_foldable(scene: dict) -> list[str]:
    """List the folded boxes a click would unfold, largest first; read either version's JSON."""
    out = []
    for n in scene["nodes"]:
        p = n.get("projection", n)
        if p.get("foldable") and not p.get("container"):
            out.append((-(p.get("leaves") or 0), n["id"]))
    return [x for _, x in sorted(out)]


def _list_boxes(scene: dict) -> set[str]:
    return {n["id"] for n in scene["nodes"]}


def _make_queries(scene: dict, unfold: int) -> list[tuple[str, dict]]:
    expanded = (scene.get("state") or {}).get("expanded") or []
    queries = [(f"p={p}", {"p": p}) for p in ("default", "folded", "unfolded")]
    queries += [(f"unfold {x}", {"x": ",".join([*expanded, x])}) for x in _list_foldable(scene)[:unfold]]
    return queries


def _time(server: Server, query: dict, runs: int) -> tuple[float | None, dict | None]:
    """Time ``query`` over ``runs`` runs after a warm-up and return the median time and the scene,
    or (None, None) if the server took too long."""
    try:
        scene, _ = server.fetch_scene(query)  # warm-up: the server may cache the dataset
    except TimeoutError:
        return None, None
    times = []
    for _ in range(runs):
        scene, ms = server.fetch_scene(query)
        times.append(ms)
    return statistics.median(times), scene


_IGNORED = {("stats", "ms"), ("dataset", "repo")}  # timings; the dataset's path differs per version
_RENAMED = {"label": {"text": "label", "icons": "icons"}, "hover": {"text": "info"},  # format 1 -> the flat fields
            "review": {"card": "review", "items": "items"}, "items": {"items": "items"},
            "verdicts": {"verdicts": "verdicts", "verdict": "verdict"}}


def _flatten_element(x: dict) -> dict:
    """Convert a box or edge of viewer JSON format 1 to the flat fields of the format before it."""
    out = {}
    for k, v in x.items():
        if k == "projection" and isinstance(v, dict):
            out.update(v)
        elif k in _RENAMED and isinstance(v, dict):
            for kk, vv in v.items():
                out[_RENAMED[k].get(kk, f"{k}.{kk}")] = vv
        elif k == "change":
            out["diff"] = v
        else:
            out[k] = v
    return out


def _canonical_scene(sc: dict) -> dict:
    """Viewer JSON with boxes by id and edges by key, so they are compared one by one."""
    if "error" in sc:
        return sc
    flat = _flatten_element if sc.get("format") else (lambda x: x)
    return {**{k: v for k, v in sc.items() if k not in ("nodes", "edges", "format", "overlays")},
            "nodes": {n["id"]: flat(n) for n in sc["nodes"]},
            "edges": {e["key"]: flat(e) for e in sc["edges"]}}


def _list_differences(a, b, path: tuple = ()):
    """(path, a, b) for each place two JSON values differ; a missing side is ``...``."""
    if path[-2:] in _IGNORED or a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            yield from _list_differences(a.get(k, ...), b.get(k, ...), (*path, k))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(isinstance(x, dict) for x in a + b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _list_differences(x, y, (*path, i))
    else:
        yield path, a, b


def _pattern(path: tuple) -> str:
    """Reduce a difference's path to where in the answer it is, leaving out the box, edge and list index."""
    out = []
    for i, p in enumerate(path):
        keyed = i > 0 and path[i - 1] in ("nodes", "edges")
        out.append("[]" if keyed or isinstance(p, int) else f".{p}")
    return "".join(out).lstrip(".") or "(the answer)"


def _short(v, n: int = 100) -> str:
    t = "(missing)" if v is ... else json.dumps(v, ensure_ascii=False)
    return t if len(t) <= n else t[: n - 1] + "…"


def _is_empty(v) -> bool:
    return v is None or v is False or v == 0 or v in ("", [], {})


def _report_json(name: str, a, b, labels: list[str]) -> list[str]:
    """Markdown lines on how two answers differ, one per kind of difference: values that
    differ; fields only one version sends, with a value; fields one version sends empty
    (null, 0, false, "", [], {}) where the other sends none, all on one line."""
    diffs = list(_list_differences(a, b))
    if not diffs:
        return [f"- {name}: same"]
    first: dict[str, tuple] = {}
    counts = Counter()
    empty: dict[str, set[str]] = {labels[0]: set(), labels[1]: set()}
    for path, x, y in diffs:
        p = _pattern(path)
        if (x is ... and _is_empty(y)) or (y is ... and _is_empty(x)):
            empty[labels[1] if x is ... else labels[0]].add(p)
            continue
        counts[p] += 1
        first.setdefault(p, (path, x, y))
    lines = [f"- {name}: {len(diffs)} differences"]
    for p, n in sorted(counts.items(), key=lambda kv: (first[kv[0]][1] is ... or first[kv[0]][2] is ..., -kv[1], kv[0])):
        path, x, y = first[p]
        where = next((str(path[i + 1]) for i, q in enumerate(path[:-1]) if q in ("nodes", "edges")), None)
        eg = f" (e.g. {where})" if where else ""
        if x is ... or y is ...:
            lines.append(f"  - `{p}` ×{n}{eg}: only {labels[0] if y is ... else labels[1]}, {_short(y if x is ... else x)}")
        else:
            lines.append(f"  - `{p}` ×{n}{eg}: {labels[0]} {_short(x)}; {labels[1]} {_short(y)}")
    for label, fields in empty.items():
        if fields:
            lines.append(f"  - empty, only {label}: " + ", ".join(f"`{f}`" for f in sorted(fields)))
    return lines


def _report_text(name: str, a: str | dict, b: str | dict, labels: list[str]) -> list[str]:
    """Markdown lines on how two text answers differ: how many lines, and the first few."""
    if a == b:
        return [f"- {name}: same"]
    if not (isinstance(a, str) and isinstance(b, str)):  # an error on one side
        return [f"- {name}: {labels[0]} {_short(a)}; {labels[1]} {_short(b)}"]
    diff = difflib.unified_diff(a.splitlines(), b.splitlines(), labels[0], labels[1], n=0, lineterm="")
    changed = [line for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    lines = [f"- {name}: {len(changed)} lines differ"]
    lines += [f"  - `{line[:140]}`" for line in changed[:6]]
    return lines


def _compare(servers: dict[str, Server], ds: Dataset) -> list[str]:
    """Ask both versions the same questions on one dataset; return Markdown lines on how they differ."""
    labels = list(servers)
    a, b = servers.values()
    presets = ["default", "folded", "unfolded"] + (["changes"] if ds.is_change else [])
    out = [f"### {ds.name}", ""]
    for p in presets:
        out += _report_json(f"`/api/scene p={p}`", _canonical_scene(a.ask("/api/scene", {"p": p})),
                            _canonical_scene(b.ask("/api/scene", {"p": p})), labels)
    out += _report_text("`/api/mermaid`", a.ask("/api/mermaid", {}, as_json=False),
                        b.ask("/api/mermaid", {}, as_json=False), labels)
    if ds.is_change:
        out += _report_json("`/api/changes`", a.ask("/api/changes", {}), b.ask("/api/changes", {}), labels)
        out += _report_text("`/api/brief`", a.ask("/api/brief", {}, as_json=False), b.ask("/api/brief", {}, as_json=False), labels)
    return out + [""]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repos", nargs="*", default=REPOS)
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--release", default="0.7.0", help="the PyPI release to compare against")
    which.add_argument("--ref", help="a git ref of this repository to compare against instead")
    ap.add_argument("--work", type=Path, default=Path.home() / ".cache" / "nolli-compare")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--unfold", type=int, default=6, help="containers to unfold one at a time")
    ap.add_argument("--diff", nargs=3, action="append", default=[], metavar=("REPO", "BASE", "HEAD"),
                    help="also a change dataset: REPO between the revisions BASE and HEAD (repeatable)")
    ap.add_argument("--compare", action="store_true", help="list how the answers differ instead of timing them")
    args = ap.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.ref:
        commit, base = _install_ref(args.work, args.ref)
        base_label, base_key = f"{args.ref} ({commit})", commit
    else:
        base = _install_release(args.work, args.release)
        base_label, base_key = f"release {args.release}", f"release-{args.release}"
    head_key = _checkout_version()
    versions = {base_label: base, f"this checkout ({head_key})": Path(sys.executable).with_name("nolli")}
    keys = dict(zip(versions, (base_key, head_key)))

    for key in keys.values():  # a dirty checkout's store may hold models of other code under the same version
        if key.endswith("-dirty"):
            shutil.rmtree(args.work / "store" / key, ignore_errors=True)
    datasets = [Dataset(_clone(args.work, r)) for r in args.repos]
    datasets += [Dataset(_clone(args.work, r, history=True), base, head) for r, base, head in args.diff]
    if args.compare:
        print(f"## {' vs '.join(versions)}\n", flush=True)
    else:
        print(f"| repository | request | {' | '.join(versions)} | ratio | same boxes |")
        print("|---|---|" + "---:|" * len(versions) + "---:|---|")
    for ds in datasets:
        servers = {}
        for label, nolli in versions.items():
            out = args.work / "data" / keys[label] / ds.name
            if keys[label].endswith("-dirty") or not any((out / f).exists() for f in ("key.json", "model.json")):
                if out.exists():
                    shutil.rmtree(out)
                ds.build(nolli, out, cwd=args.work, env=_make_env(args.work / "store" / keys[label]))
            servers[label] = Server(nolli, out, _make_env(args.work / "store" / keys[label]))
        try:
            if args.compare:
                print("\n".join(_compare(servers, ds)), flush=True)
                continue
            first = next(iter(servers.values()))
            queries = _make_queries(first.fetch_scene({"p": "default"})[0], args.unfold)
            for name, query in queries:
                results = {label: _time(s, query, args.runs) for label, s in servers.items()}
                ms = [results[v][0] for v in versions]
                cells = " | ".join("timed out" if m is None else f"{m:.0f} ms" for m in ms)
                if None in ms:
                    print(f"| {ds.name} | {name} | {cells} | | |", flush=True)
                    continue
                boxes = [_list_boxes(results[v][1]) for v in versions]
                labels = list(versions)
                same = "yes" if all(b == boxes[0] for b in boxes) else "no"
                if same == "no":
                    for label, mine, other in ((labels[0], boxes[0], boxes[-1]), (labels[-1], boxes[-1], boxes[0])):
                        if mine - other:
                            print(f"  {ds.name} {name}: only in {label}: {sorted(mine - other)[:8]}", file=sys.stderr)
                print(f"| {ds.name} | {name} | {cells} | {ms[-1] / ms[0]:.2f} | {same} |", flush=True)
        finally:
            for s in servers.values():
                s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
