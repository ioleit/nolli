"""Serve the nolli viewer and the models of a store.

    nolli view …                                      (installed: builds a model, then serves)
    python -I viewer/serve.py --data ~/data/nolli [--host 127.0.0.1] [--port 8720]   (a checkout)

Static files come from this directory. Models are named as the store names them (for nolli
view, the directories under --data; nolli/store.py): ``/api/list`` lists them, newest first;
``/api/scene?d=<name>&...`` returns the scene for a scene state (``nolli/scene.py``; the
parameters are the viewer's location-hash keys), ``/api/mermaid?d=<name>&...`` the same
scene as Mermaid (``nolli/mermaid.py``; ``nodim=1``: unchanged not faded; ``format=json``:
``{mermaid, ids}``, ids mapping each Mermaid node id to its scene id); ``/api/node?d=<name>&id=<entity>``
returns the details of one box in a change's model (``nolli/changes.py``), ``/api/changes?d=``
its numbered changes and the review's verdicts, ``/api/brief?d=`` what a review agent gets
(Markdown), and ``POST /api/verdicts?d=`` stores a review's verdicts. ``/api/review?d=`` says
where the verdicts came from and whether a review agent can run here; ``POST /api/review?d=``
starts one (it needs ANTHROPIC_API_KEY and the change's commits, viewer/reviews.py),
``POST /api/review/cancel?d=`` stops it. Every read of a model goes through the store, which
may refuse it (NotAllowed): the answer is then 404, as for no such model.

What the server works with (``Services``: the store, where review agents get commits, the
review runs) lives on the server object, which every request's handler reaches as
``self.server``. Needs only the standard library, but for the review agent. Installed, this
is ``nolli.viewer.serve``; run as a script from a checkout, it imports the nolli package of
that checkout, not an installed copy.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote

HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE.parent))  # run as a script: the nolli package of this checkout

from nolli.derived import make_review_brief  # noqa: E402
from nolli.changes import change_items, details  # noqa: E402
from nolli.mermaid import ids as mermaid_ids, render  # noqa: E402
from nolli.model import Model  # noqa: E402
from nolli.review import read_verdicts, resolve_verdicts  # noqa: E402
from nolli.scene import scene, state_from_query  # noqa: E402
from nolli.store import Change, DirectoryStore, LocalRepository, ModelKey, ModelStore, NotAllowed, Sources  # noqa: E402

try:  # installed: nolli.viewer.reviews; from a checkout, reviews.py beside this file
    from .reviews import Reviews, provenance, save_verdicts
except ImportError:
    from reviews import Reviews, provenance, save_verdicts  # type: ignore[no-redef]


@dataclass
class Services:
    """What a viewer server works with. A service built on it passes its own (a subclass, for
    more)."""

    store: ModelStore
    reviews: Reviews
    sources: Sources | None = None  # where a review agent gets a change's commits (all models)
    repos: dict[str, Path] = field(default_factory=dict)  # or, by model name, the local repository it came from
    uploads: bool = True  # POST /api/verdicts stores a review's verdicts

    def find_sources(self, name: str) -> Sources | None:
        """Where a review agent run on the model ``name`` gets the change's commits."""
        return LocalRepository(self.repos[name]) if name in self.repos else self.sources


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler, services: Services):
        super().__init__(address, handler)
        self.services = services


def preload(store: ModelStore, name: str) -> None:
    """Start reading a model in the background, for the viewer's first request."""
    def load() -> None:
        try:
            key = store.find_key(name)
            if key:
                store.load_model(key)
        except (NotAllowed, OSError, ValueError):
            pass

    threading.Thread(target=load, daemon=True).start()


def load_verdicts(store: ModelStore, key: ModelKey) -> list[dict]:
    """The model's verdicts, if it has valid ones."""
    review = store.load_review(key)
    if review is None:
        return []
    try:
        return read_verdicts(json.dumps(review.verdicts))
    except ValueError:
        return []


class Handler(SimpleHTTPRequestHandler):
    server: Server

    @property
    def services(self) -> Services:
        return self.server.services

    def query(self) -> dict:
        return dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))

    def open_model(self, name: str | None) -> tuple[ModelKey, Model] | None:
        """The model ``name``, through the store; None if there is none, or the store refuses it."""
        store = self.services.store
        try:
            key = store.find_key(name or "")
            m = store.load_model(key) if key else None
        except NotAllowed:
            return None
        return (key, m) if key and m is not None else None

    def review_status(self, name: str, key: ModelKey, m: Model) -> dict:
        """What the review panel shows: the verdicts' origin, the run under way or the last one,
        and whether (and why not) a run can be started here."""
        reviews = self.services.reviews
        why = reviews.reviewer.available()
        if why is None and not isinstance(key, Change):
            why = "dataset"
        if why is None and self.services.find_sources(name) is None:
            why = "repo"
        job = reviews.job(name)
        return {"verdicts": provenance(self.services.store, key), "run": job.status() if job else None,
                "can_run": why is None, "why_not": why, "uploads": self.services.uploads,
                "model": reviews.reviewer.model(), "server": "view"}

    def start_review(self, name: str, key: ModelKey) -> None:
        sources = self.services.find_sources(name)
        if isinstance(key, Change) and sources is not None:
            self.services.reviews.start(name, self.services.store, key, sources)

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
            self.send_json([{"name": n} for n in self.services.store.list_names()])
            return
        if not path.startswith("/api/"):
            super().do_GET()
            return
        q = self.query()
        t0 = time.perf_counter()
        found = self.open_model(q.get("d"))
        if found is None:
            self.send_json({"error": f"no dataset {q.get('d')!r}"}, 404)
            return
        key, m = found
        store = self.services.store
        t1 = time.perf_counter()
        if path == "/api/scene":
            try:
                out = scene(m, state_from_query(q), load_verdicts(store, key))
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            meta = m.meta or {}
            out["dataset"] = {"name": q["d"], "repo": meta.get("repo"), "commit": meta.get("commit"), "files": meta.get("files"),
                              "entities": len(m.entities), "relations": len(m.relations)}
            out["stats"]["ms"] = {"load": round((t1 - t0) * 1000), "scene": round((time.perf_counter() - t1) * 1000)}
            self.send_json(out)
        elif path == "/api/mermaid":
            try:
                sc = scene(m, state_from_query(q))
                text = render(sc, fade=q.get("nodim") != "1")
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            if q.get("format") == "json":
                self.send_json({"mermaid": text, "ids": mermaid_ids(sc)})
            else:
                self.send_text(text)
        elif path == "/api/changes":
            diff = bool(m.meta.get("diff"))
            self.send_json({"items": change_items(m) if diff else [],
                            "verdicts": resolve_verdicts(m, load_verdicts(store, key)) if diff else []})
        elif path == "/api/node":
            d = details(m, q.get("id", ""))
            if d is None:
                self.send_json({"error": f"no entity {q.get('id', '')!r}"}, 404)
                return
            self.send_json(d)
        elif path == "/api/brief":
            if not m.meta.get("diff"):
                self.send_json({"error": "not a change"}, 404)
                return
            self.send_text(make_review_brief(m), "text/markdown; charset=utf-8")
        elif path == "/api/review":
            self.send_json(self.review_status(q["d"], key, m))
        else:
            self.send_json({"error": f"no {path}"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """POST /api/verdicts?d=<name>: store a review's verdicts (validated); /api/review?d=:
        start a review agent; /api/review/cancel?d=: stop it."""
        path = unquote(self.path.split("?")[0])
        q = self.query()
        found = self.open_model(q.get("d"))
        if path in ("/api/review", "/api/review/cancel") and found is not None:
            key, m = found
            st = self.review_status(q["d"], key, m)
            if path == "/api/review/cancel":
                self.services.reviews.cancel(q["d"])
            elif not st["can_run"]:
                self.send_json({"error": f"no review agent here ({st['why_not']})", **st}, 409)
                return
            else:
                self.start_review(q["d"], key)
            self.send_json(self.review_status(q["d"], key, m))
            return
        uploads = self.services.uploads
        if path != "/api/verdicts" or found is None or not uploads:
            self.send_json({"error": "POST /api/verdicts?d=<dataset> only" if uploads else "verdicts are not stored here"},
                           404 if uploads else 405)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        try:
            v = read_verdicts(body)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        save_verdicts(self.services.store, found[0], v, {"by": "upload"})
        self.send_json({"saved": len(v)})

    def send_text(self, text: str, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def make_server(data: Path | ModelStore, host: str = "127.0.0.1", port: int = 8720,
                repos: dict[str, Path] | None = None) -> Server:
    """A server for the viewer and the models of ``data`` (a store, or a directory of models),
    not yet serving. Port 0 picks a free one. ``repos``: the local repository of a model (by
    name), which a review agent started here reads."""
    store = DirectoryStore(Path(data)) if isinstance(data, (str, Path)) else data
    work = store.root / ".reviews" if isinstance(store, DirectoryStore) else None
    services = Services(store, Reviews(work=work), repos=dict(repos or {}))
    return Server((host, port), partial(Handler, directory=str(HERE)), services)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8720)
    args = ap.parse_args()
    srv = make_server(Path(args.data), args.host, args.port)
    print(f"serving {HERE} + data {args.data} on http://{args.host}:{args.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
