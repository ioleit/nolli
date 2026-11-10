"""Times /api/scene of this checkout against a nolli release from PyPI, or against a git ref.

    python dev/compare_release.py [--release 0.7.0 | --ref origin/main] [--work DIR] [--runs 5]
                                  [repo URL or path ...]

Each version extracts the same repositories with its own ``nolli extract`` and serves them
with its own ``nolli view``; the same requests go to both: the presets, and unfolding
containers one at a time from the default projection, as the viewer asks when you click. The
table gives the median time of each request and whether both versions drew the same boxes.
The release (or the ref, checked out with ``git worktree``) is installed into a virtual
environment under the work directory. Extracted datasets are kept per version (the release
number, or the commit of the ref or of this checkout, with ``-dirty`` for uncommitted
changes, re-extracted every run), so a changed format is never read by the other version.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPOS = [
    "https://github.com/pallets/click",
    "https://github.com/pallets/flask",
    "https://github.com/ioleit/nolli",
    "https://github.com/fastapi/full-stack-fastapi-template",
]


def _run(*cmd: str | Path, cwd: Path | None = None) -> None:
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, stdout=subprocess.DEVNULL)


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
    if not tree.exists():
        _run("git", "worktree", "add", "-q", "--detach", tree, commit, cwd=root)
    if not (venv / "bin" / "nolli").exists():
        _run(sys.executable, "-m", "venv", venv)
        _run(venv / "bin" / "pip", "install", "-q", tree)
    return commit, venv / "bin" / "nolli"


def _checkout_version() -> str:
    """This checkout's commit, with ``-dirty`` when the working tree has uncommitted changes."""
    root = Path(__file__).resolve().parent.parent
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + ("-dirty" if git("status", "--porcelain", "--", "nolli") else "")


def _clone(work: Path, repo: str) -> Path:
    if Path(repo).is_dir():
        return Path(repo).resolve()
    out = work / "repos" / repo.rstrip("/").rsplit("/", 1)[-1]
    if not out.exists():
        _run("git", "clone", "-q", "--depth", "1", repo, out)
    return out


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """One version's ``nolli view`` on one dataset directory."""

    def __init__(self, nolli: Path, dataset: Path) -> None:
        self.nolli, self.port = nolli, _find_free_port()
        self.proc = subprocess.Popen([str(nolli), "view", str(dataset), "--no-open", "--port", str(self.port)],
                                     cwd=dataset.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 60
        while True:
            try:
                names = [d["name"] for d in json.loads(self.get("/api/list"))]
                if dataset.name not in names:
                    raise RuntimeError(f"{nolli} view serves {names}, not {dataset.name}")
                self.name = dataset.name
                return
            except OSError:
                if time.time() > deadline or self.proc.poll() is not None:
                    raise RuntimeError(f"{nolli} view {dataset} did not start") from None
                time.sleep(0.2)

    def get(self, path: str) -> bytes:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=60) as r:
            return r.read()

    def fetch_scene(self, query: dict) -> tuple[dict, float]:
        t0 = time.perf_counter()
        body = self.get("/api/scene?" + urllib.parse.urlencode({"d": self.name, **query}))
        return json.loads(body), (time.perf_counter() - t0) * 1000

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait()


def _list_foldable(scene: dict) -> list[str]:
    """The folded boxes a click would unfold, largest first (either version's JSON)."""
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
    """The median time of ``query``, and the scene; (None, None) if the server took too long."""
    try:
        scene, _ = server.fetch_scene(query)  # warm-up: the server may cache the dataset
    except TimeoutError:
        return None, None
    times = []
    for _ in range(runs):
        scene, ms = server.fetch_scene(query)
        times.append(ms)
    return statistics.median(times), scene


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repos", nargs="*", default=REPOS)
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--release", default="0.7.0", help="the PyPI release to compare against")
    which.add_argument("--ref", help="a git ref of this repository to compare against instead")
    ap.add_argument("--work", type=Path, default=Path.home() / ".cache" / "nolli-compare")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--unfold", type=int, default=6, help="containers to unfold one at a time")
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

    print(f"| repository | request | {' | '.join(versions)} | ratio | same boxes |")
    print("|---|---|" + "---:|" * len(versions) + "---:|---|")
    for repo in args.repos:
        src = _clone(args.work, repo)
        servers = {}
        for label, nolli in versions.items():
            out = args.work / "data" / keys[label] / src.name
            if keys[label].endswith("-dirty") or not any((out / f).exists() for f in ("key.json", "model.json")):
                if out.exists():
                    shutil.rmtree(out)
                _run(nolli, "extract", src, "-o", out, cwd=args.work)
            servers[label] = Server(nolli, out)
        try:
            first = next(iter(servers.values()))
            queries = _make_queries(first.fetch_scene({"p": "default"})[0], args.unfold)
            for name, query in queries:
                results = {label: _time(s, query, args.runs) for label, s in servers.items()}
                ms = [results[v][0] for v in versions]
                cells = " | ".join("timed out" if m is None else f"{m:.0f} ms" for m in ms)
                if None in ms:
                    print(f"| {src.name} | {name} | {cells} | | |", flush=True)
                    continue
                boxes = [_list_boxes(results[v][1]) for v in versions]
                same = "yes" if all(b == boxes[0] for b in boxes) else "no"
                if same == "no":
                    print(f"  {src.name} {name}: only in one version: {sorted(boxes[0] ^ boxes[-1])[:8]}", file=sys.stderr)
                print(f"| {src.name} | {name} | {cells} | {ms[-1] / ms[0]:.2f} | {same} |", flush=True)
        finally:
            for s in servers.values():
                s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
