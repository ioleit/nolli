"""Serve the nolli viewer and the models of a store.

    nolli view …                                      (installed: builds a model, then serves)
    python -m nolli.server.serve --data ~/data/nolli [--host 127.0.0.1] [--port 8720]

Static files are the viewer's (``viewer/``, installed as ``nolli.viewer``). Models are named as the store names them (for nolli
view, the directories under --data; nolli/store/): ``/api/list`` lists them, newest first;
``/api/scene?d=<name>&...`` returns the scene a scene state projects (``parse_state`` here
reads the viewer's location-hash keys; ``nolli/transformations/projection.py``), as JSON with every
overlay (``nolli/formats/viewer.py``), ``/api/mermaid?d=<name>&...`` the same scene as Mermaid
(``nolli/formats/mermaid.py``; ``nodim=1``: unchanged not faded; ``format=json``: ``{mermaid,
ids}``, ids mapping each Mermaid node id to its scene id); ``/api/node?d=<name>&id=<node>``
returns the details of one box in a change (``nolli/transformations/merge.py``), ``/api/changes?d=``
its numbered changes and the review's verdicts, ``/api/brief?d=`` what a review agent gets
(Markdown), and ``POST /api/verdicts?d=`` stores a review's verdicts. ``/api/review?d=`` says
where the verdicts came from and whether a review agent can run here; ``POST /api/review?d=``
starts one (it needs ANTHROPIC_API_KEY and the change's commits, reviews.py),
``POST /api/review/cancel?d=`` stops it. Every read of a model goes through the store, which
may refuse it (NotAllowed): the answer is then 404, as for no such model.

What the server works with (``Services``: the store, where review agents get commits, the
review runs) lives on the server object, which every request's handler reaches as
``self.server``. Needs only the standard library, but for the review agent.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote

import nolli.viewer  # noqa: E402  (the page, its scripts and styles)

HERE = Path(nolli.viewer.__file__).resolve().parent  # the viewer's files

from nolli.datasets import Datasets  # noqa: E402
from nolli.formats.mermaid import list_ids, render  # noqa: E402
from nolli.formats.viewer import export_scene  # noqa: E402
from nolli.model.keys import ChangeKey, ModelKey, NotAllowed  # noqa: E402
from nolli.model.scene import Scene  # noqa: E402
from nolli.review.brief import make_review_brief  # noqa: E402
from nolli.review.changes import detail_box, list_changes  # noqa: E402
from nolli.review.verdicts import describe_verdicts, find_diagram_ids, read_verdicts  # noqa: E402
from nolli.sources import LocalRepository, Sources  # noqa: E402
from nolli.store import DirectoryStore, ModelStore  # noqa: E402
from nolli.transformations.projection import DEFAULT_UTILITY_IN, PRESETS, SceneState  # noqa: E402

from .reviews import Reviews  # noqa: E402


@dataclass
class Services:
    """What a viewer server works with. A service built on it passes its own (a subclass, for
    more)."""

    store: ModelStore
    reviews: Reviews
    sources: Sources | None = None  # where a review agent gets a change's commits (all models)
    repos: dict[str, Path] = field(default_factory=dict)  # or, by model name, the local repository it came from
    uploads: bool = True  # POST /api/verdicts stores a review's verdicts

    @property
    def datasets(self) -> Datasets:
        return Datasets(self.store, self.sources)

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
                Datasets(store).open_scene(key)
        except (NotAllowed, OSError, ValueError):
            pass

    threading.Thread(target=load, daemon=True).start()


def parse_state(q: dict[str, str]) -> SceneState:
    """The viewer's URL parameters (the same keys as its location hash) as a scene state."""
    st = SceneState()
    if q.get("x"):
        st.expanded = [x for x in q["x"].split(",") if x]
    elif q.get("p") in PRESETS:
        st.preset = q["p"]
    if q.get("r") is not None:
        st.roles = [r for r in q["r"].split(",") if r]
    if q.get("b"):
        st.budget = int(q["b"])
    if q.get("g") is not None:
        st.groups = [x for x in q["g"].split(",") if x]
    st.utilities = 0 if q.get("nu") == "1" else int(q["ut"]) if q.get("ut") else DEFAULT_UTILITY_IN
    if q.get("s") in ("base", "head"):
        st.side = q["s"]
    if q.get("ml"):
        st.max_lines = max(1, int(q["ml"]))
    return st


class Handler(SimpleHTTPRequestHandler):
    server: Server

    @property
    def services(self) -> Services:
        return self.server.services

    def query(self) -> dict:
        return dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))

    def open_model(self, name: str | None) -> tuple[ModelKey, Scene] | None:
        """The scene of the model ``name``, through the store; None if there is none, or the store
        refuses it."""
        datasets = self.services.datasets
        try:
            key = datasets.find_key(name or "")
            sc = datasets.open_scene(key) if key else None
        except NotAllowed:
            return None
        return (key, sc) if key and sc is not None else None

    def review_status(self, name: str, key: ModelKey, m: Scene) -> dict:
        """What the review panel shows: the verdicts' origin, the run under way or the last one,
        and whether (and why not) a run can be started here."""
        reviews = self.services.reviews
        why = reviews.reviewer.available()
        if why is None and not isinstance(key, ChangeKey):
            why = "dataset"
        if why is None and self.services.find_sources(name) is None:
            why = "repo"
        job = reviews.job(name)
        return {"verdicts": self.services.datasets.find_provenance(key), "run": job.status() if job else None,
                "can_run": why is None, "why_not": why, "uploads": self.services.uploads,
                "model": reviews.reviewer.model(), "server": "view"}

    def start_review(self, name: str, key: ModelKey) -> None:
        sources = self.services.find_sources(name)
        if isinstance(key, ChangeKey) and sources is not None:
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
        key, sc = found
        t1 = time.perf_counter()
        change = isinstance(key, ChangeKey)
        if path == "/api/scene":
            try:
                out = export_scene(self.services.datasets.draw(sc, parse_state(q)))
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            meta = sc.model.meta or {}
            out["dataset"] = {"name": q["d"], "repo": meta.get("repo"), "commit": meta.get("commit"), "files": meta.get("files"),
                              "entities": len(sc.model.kinds), "relations": len(sc.model.edges)}
            out["stats"]["ms"] = {"load": round((t1 - t0) * 1000), "scene": round((time.perf_counter() - t1) * 1000)}
            self.send_json(out)
        elif path == "/api/mermaid":
            try:
                ps = self.services.datasets.draw(sc, parse_state(q))
                text = render(ps, fade=q.get("nodim") != "1")
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            if q.get("format") == "json":
                self.send_json({"mermaid": text, "ids": list_ids(ps)})
            else:
                self.send_text(text)
        elif path == "/api/changes":
            self.send_json({"items": list_changes(sc) if change else [],
                            "verdicts": describe_verdicts(sc, self.services.datasets.load_verdicts(sc)) if change else []})
        elif path == "/api/node":
            d = detail_box(sc, q.get("id", "")) if change else None
            if d is None:
                self.send_json({"error": f"no entity {q.get('id', '')!r}"}, 404)
                return
            self.send_json(d)
        elif path == "/api/brief":
            if not change:
                self.send_json({"error": "not a change"}, 404)
                return
            self.send_text(make_review_brief(sc), "text/markdown; charset=utf-8")
        elif path == "/api/review":
            self.send_json(self.review_status(q["d"], key, sc))
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
        if not isinstance(found[0], ChangeKey):
            self.send_json({"error": "not a change"}, 400)
            return
        try:
            v = read_verdicts(body, find_diagram_ids(found[1]))
        except (ValueError, KeyError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        self.services.datasets.keep_verdicts(found[0], v, {"by": "upload"})
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
