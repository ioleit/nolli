"""Serve the nolli viewer and one or more extracted datasets.

    python -I serve.py --data ~/data/nolli [--host 127.0.0.1] [--port 8720]

Static files come from this directory; ``/data/<name>/<file>`` serves the
dataset directories under --data; ``/api/list`` lists them, newest first.
"""

from __future__ import annotations

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    data_root: Path = HERE / "data"

    def send_json(self, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
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

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8720)
    args = ap.parse_args()
    Handler.data_root = Path(args.data).expanduser().resolve()
    srv = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(HERE)))
    print(f"serving {HERE} + data {Handler.data_root} on http://{args.host}:{args.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
