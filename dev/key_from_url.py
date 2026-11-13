"""Print the key of the model a viewer page shows, projected as the page's URL says:

    uv run python dev/key_from_url.py '<viewer URL>'
    KEY=$(uv run python dev/key_from_url.py 'http://127.0.0.1:8720/?t=#p=folded')

The page names the model it shows (``<meta name="nolli-key">``, nolli/server/serve.py), so the
script asks the running viewer for it: ``?t=`` may be a branch that only the server resolves. The
URL's fragment (``#…``) is the projection on screen, in the projection's query keys
(``ProjectionSpec.from_query``); the viewer's own keys there (theme, tab, item) are left out. The
result is ``<model key>~project:<spec>``, which ``nolli comments add`` takes as the model the
comments were made on. A URL without a fragment gives the model's key alone.

Prerequisites: the viewer serving the URL (nolli view), and this checkout's nolli importable
(uv run). Quote the URL: the shell reads ``&`` and ``#``.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from html import unescape
from urllib.parse import urldefrag

from nolli.logic.transformations.merge import presets
from nolli.logic.transformations.projection import ProjectionSpec
from nolli.model.keys import ModelKey, ProjectionKey

META = re.compile(r'<meta name="nolli-key" content="([^"]*)">')


def find_model_key(page_url: str) -> str:
    """Ask the viewer for the page at ``page_url`` and return the key of the model it shows. Raise
    SystemExit with the reason if there is no page or it shows no model yet."""
    try:
        with urllib.request.urlopen(page_url, timeout=30) as res:
            html = res.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"key_from_url: cannot open {page_url} ({exc}); is the viewer running (nolli view)?") from None
    m = META.search(html)
    if m is None:
        raise SystemExit(f"key_from_url: {page_url} shows no model (still building, or not a viewer page); "
                         "open it in the browser first")
    return unescape(m.group(1))


def make_key(model_key: str, fragment: str) -> str:
    """Return the key of the projection of ``model_key`` the URL fragment describes, or the model's
    key if the fragment is empty."""
    if not fragment:
        return model_key
    spec = ProjectionSpec.parse(fragment, presets.PRESETS)
    return ProjectionKey(ModelKey.parse(model_key), spec.format()).format_key()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="a viewer page's URL, with the #… the browser shows")
    args = ap.parse_args(argv)
    page, fragment = urldefrag(args.url)
    print(make_key(find_model_key(page), fragment))
    return 0


if __name__ == "__main__":
    sys.exit(main())
