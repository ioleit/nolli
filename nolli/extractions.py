"""Extractions of commits, kept by commit: a commit's files never change, so neither does
what one version of nolli extracts from them. A pull request's base is extracted once for
all its pushes, and a repository's page and a change on top of it share that commit's side.

One file per commit, ``<sha>.json``: the version of nolli that wrote it, the model as
extracted (before a build names its root or adds its own meta) and the extractors' notes
that a dataset's report keeps. A file of another version is a miss. Files are written
whole or not at all, so two builds extracting the same commit at once both end well.
Using a file touches it, which is what a cache's retention (nolli/retention.py) goes by.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .model import Model

DROPPED_NOTES = ("py-imports", "js-imports", "py-aliases", "ts-exports")  # matching's working state, not for a report


class Extractions:
    def __init__(self, root: Path, version: str):
        self.root, self.version = root, version
        root.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        return self.root / f"{sha}.json"

    def has(self, sha: str) -> bool:
        """A file for ``sha`` (of some version: ``get`` says whether it is of this one)."""
        return self.path(sha).is_file()

    def get(self, sha: str) -> tuple[Model, dict] | None:
        """The model and notes extracted from ``sha`` before, by this version of nolli."""
        f = self.path(sha)
        try:
            d = json.loads(f.read_text())
            if d.get("nolli") != self.version:
                return None
            os.utime(f)
            return Model.from_dict(d["model"]), d.get("notes", {})
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, sha: str, m: Model, notes: dict) -> None:
        kept = {k: v for k, v in notes.items() if k not in DROPPED_NOTES}
        f, tmp = self.path(sha), self.root / f".{sha}.{os.getpid()}.{id(m)}.json"
        tmp.write_text(json.dumps({"nolli": self.version, "model": m.to_dict(), "notes": kept}, default=str))
        tmp.replace(f)
