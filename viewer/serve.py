"""Serve the nolli viewer and one or more extracted datasets.

    nolli view …                                      (installed: builds a dataset, then serves)
    python -I viewer/serve.py --data ~/data/nolli [--host 127.0.0.1] [--port 8720]   (a checkout)

Static files come from this directory; ``/data/<name>/<file>`` serves the
dataset directories under --data; ``/api/list`` lists them, newest first;
``/api/scene?d=<name>&...`` returns the scene for a scene state (``nolli/scene.py``;
the parameters are the viewer's location-hash keys), ``/api/mermaid?d=<name>&...`` the same
scene as Mermaid (``nolli/mermaid.py``; ``nodim=1``: unchanged not faded; ``format=json``:
``{mermaid, ids}``, ids mapping each Mermaid node id to its scene id); ``/api/node?d=<name>&id=<entity>``
returns the details of one box in a diff dataset (``nolli/changes.py``), ``/api/changes?d=``
its numbered changes and the review's verdicts, and ``POST /api/verdicts?d=`` stores a
review's verdicts.json next to the dataset. ``/api/review?d=`` says where the verdicts came
from and whether a review agent can run here; ``POST /api/review?d=`` starts one (it needs
ANTHROPIC_API_KEY and the change's repository, viewer/reviews.py), ``POST /api/review/cancel?d=``
stops it. Needs only the standard library, but for the review agent. Installed,
this is ``nolli.viewer.serve``; run as a script from a checkout, it imports the nolli
package of that checkout, not an installed copy.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote

HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE.parent))  # run as a script: the nolli package of this checkout

from nolli.model import Model  # noqa: E402
from nolli.changes import change_items, details  # noqa: E402
from nolli.review import read_verdicts, resolve_verdicts  # noqa: E402
from nolli.mermaid import ids as mermaid_ids, render  # noqa: E402
from nolli.scene import scene, state_from_query  # noqa: E402

try:  # installed: nolli.viewer.reviews; from a checkout, reviews.py beside this file
    from .reviews import Reviews, provenance, store
except ImportError:
    from reviews import Reviews, provenance, store  # type: ignore[no-redef]

_models: dict[str, tuple[float, Model]] = {}
_loading: dict[str, threading.Lock] = {}  # one per model.json: a request waits for a load under way
_models_lock = threading.Lock()


def load_model(path: Path) -> Model:
    """model.json, parsed once per file version."""
    with _models_lock:
        lock = _loading.setdefault(str(path), threading.Lock())
    with lock:
        mtime = path.stat().st_mtime
        hit = _models.get(str(path))
        if hit and hit[0] == mtime:
            return hit[1]
        m = Model.load(path)
        m.roots()  # build the containment index before threads share the model
        _models[str(path)] = (mtime, m)
        return m


def preload(ds: Path) -> None:
    """Start reading a dataset's model in the background, for the viewer's first request."""
    if (ds / "model.json").is_file():
        threading.Thread(target=load_model, args=(ds / "model.json",), daemon=True).start()


def load_verdicts(ds: Path) -> list[dict]:
    """verdicts.json in a dataset directory, if there is a valid one."""
    f = ds / "verdicts.json"
    if not f.is_file():
        return []
    try:
        return read_verdicts(f.read_text())
    except (ValueError, OSError):
        return []


class Handler(SimpleHTTPRequestHandler):
    data_root: Path = HERE / "data"
    repos: dict[str, Path] = {}  # dataset name -> the repository it was built from, for a review agent
    reviews = Reviews()
    uploads = True  # POST /api/verdicts stores a review's verdicts.json

    def dataset(self) -> tuple[dict, Path | None]:
        """The query, and the dataset it names (None if there is no such dataset)."""
        q = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        ds = self.data_root / q.get("d", "")
        return q, ds if q.get("d") and "/" not in q["d"] and (ds / "model.json").is_file() else None

    def review_repo(self, ds: Path) -> Path | None:
        """The git repository a review agent reads the change's code from."""
        return self.repos.get(ds.name)

    def start_review(self, ds: Path) -> None:
        repo = self.review_repo(ds)
        self.reviews.start(ds.name, ds, lambda: repo)

    def review_status(self, ds: Path) -> dict:
        """What the review panel shows: the verdicts' origin, the run under way or the last one,
        and whether (and why not) a run can be started here."""
        why = self.reviews.reviewer.available()
        if why is None and not (ds / "review.mmd").is_file():
            why = "dataset"
        if why is None and self.review_repo(ds) is None:
            why = "repo"
        job = self.reviews.job(ds.name)
        return {"verdicts": provenance(ds), "run": job.status() if job else None,
                "can_run": why is None, "why_not": why, "uploads": self.uploads,
                "model": self.reviews.reviewer.model(), "server": "view"}

    def send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?")[0])
        if path == "/api/list":
            sets = []
            for d in self.data_root.iterdir():
                if (d / "model.json").exists():
                    meta = {}
                    try:
                        meta = json.loads((d / "report.json").read_text()).get("meta", {})
                    except (OSError, json.JSONDecodeError):
                        pass
                    sets.append({"name": d.name, "mtime": int((d / "model.json").stat().st_mtime), "meta": meta})
            sets.sort(key=lambda s: -s["mtime"])
            self.send_json(sets)
            return
        if path == "/api/scene":
            q = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            ds = self.data_root / q.get("d", "")
            if not q.get("d") or "/" in q["d"] or not (ds / "model.json").is_file():
                self.send_json({"error": f"no dataset {q.get('d')!r}"}, 404)
                return
            try:
                t0 = time.perf_counter()
                m = load_model(ds / "model.json")
                t1 = time.perf_counter()
                out = scene(m, state_from_query(q), load_verdicts(ds))
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            meta = m.meta or {}
            out["dataset"] = {"name": q["d"], "repo": meta.get("repo"), "commit": meta.get("commit"), "files": meta.get("files"),
                              "entities": len(m.entities), "relations": len(m.relations)}
            out["stats"]["ms"] = {"load": round((t1 - t0) * 1000), "scene": round((time.perf_counter() - t1) * 1000)}
            self.send_json(out)
            return
        if path == "/api/mermaid":
            q = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            ds = self.data_root / q.get("d", "")
            if not q.get("d") or "/" in q["d"] or not (ds / "model.json").is_file():
                self.send_json({"error": f"no dataset {q.get('d')!r}"}, 404)
                return
            try:
                sc = scene(load_model(ds / "model.json"), state_from_query(q))
                text = render(sc, fade=q.get("nodim") != "1")
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            if q.get("format") == "json":
                self.send_json({"mermaid": text, "ids": mermaid_ids(sc)})
                return
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/changes":
            q = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            ds = self.data_root / q.get("d", "")
            if not q.get("d") or "/" in q["d"] or not (ds / "model.json").is_file():
                self.send_json({"error": f"no dataset {q.get('d')!r}"}, 404)
                return
            m = load_model(ds / "model.json")
            diff = bool(m.meta.get("diff"))
            self.send_json({"items": change_items(m) if diff else [],
                            "verdicts": resolve_verdicts(m, load_verdicts(ds)) if diff else []})
            return
        if path == "/api/node":
            q = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            ds = self.data_root / q.get("d", "")
            if not q.get("d") or "/" in q["d"] or not (ds / "model.json").is_file():
                self.send_json({"error": f"no dataset {q.get('d')!r}"}, 404)
                return
            m = load_model(ds / "model.json")
            x = q.get("id", "")
            d = details(m, x)
            if d is None:
                self.send_json({"error": f"no entity {x!r}"}, 404)
                return
            self.send_json(d)
            return
        if path == "/api/review":
            _q, ds = self.dataset()
            if ds is None:
                self.send_json({"error": "no such dataset"}, 404)
                return
            self.send_json(self.review_status(ds))
            return
        if path.startswith("/data/"):
            rel = path[len("/data/"):]
            target = (self.data_root / rel).resolve()
            if not str(target).startswith(str(self.data_root.resolve())) or not target.is_file():
                self.send_error(404)
                return
            body = target.read_bytes()
            self.send_response(200)
            ctype = "application/json" if target.suffix == ".json" else "text/plain; charset=utf-8"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        """POST /api/verdicts?d=<name>: store a review's verdicts.json in the dataset (validated);
        /api/review?d=: start a review agent; /api/review/cancel?d=: stop it."""
        path = unquote(self.path.split("?")[0])
        _q, ds = self.dataset()
        if path in ("/api/review", "/api/review/cancel") and ds is not None:
            st = self.review_status(ds)
            if path == "/api/review/cancel":
                self.reviews.cancel(ds.name)
            elif not st["can_run"]:
                self.send_json({"error": f"no review agent here ({st['why_not']})", **st}, 409)
                return
            else:
                self.start_review(ds)
            self.send_json(self.review_status(ds))
            return
        if path != "/api/verdicts" or ds is None or not self.uploads:
            self.send_json({"error": "POST /api/verdicts?d=<dataset> only" if self.uploads else "verdicts are not stored here"},
                           404 if self.uploads else 405)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        try:
            v = read_verdicts(body)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        store(ds, v, {"by": "upload"})
        self.send_json({"saved": len(v)})

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def make_server(data: Path, host: str = "127.0.0.1", port: int = 8720, repos: dict[str, Path] | None = None) -> ThreadingHTTPServer:
    """A server for the viewer and the datasets under ``data``, not yet serving. Port 0 picks a free one.
    ``repos``: the repository of a dataset (by name), which a review agent started here reads."""
    Handler.data_root = Path(data).expanduser().resolve()
    Handler.repos = dict(repos or {})
    return ThreadingHTTPServer((host, port), partial(Handler, directory=str(HERE)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8720)
    args = ap.parse_args()
    srv = make_server(args.data, args.host, args.port)
    print(f"serving {HERE} + data {Handler.data_root} on http://{args.host}:{args.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
