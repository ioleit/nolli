"""The package's file list in pyproject.toml stays complete: every Python package
directory, and every local file the viewer loads."""

from __future__ import annotations

import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]


def test_every_package_is_listed():
    found = {".".join(p.parent.relative_to(ROOT).parts) for p in (ROOT / "nolli").rglob("__init__.py")}
    assert found | {"nolli.viewer"} == set(CONF["packages"])


def test_every_file_the_viewer_loads_is_packaged():
    viewer = ROOT / CONF["package-dir"]["nolli.viewer"]
    patterns = CONF["package-data"]["nolli.viewer"]
    pages = [viewer / "index.html", viewer / "bench.html"]
    refs = set()
    for page in pages:
        refs |= set(re.findall(r'(?:src|href)="([^":]+)"', page.read_text()))
    for js in (viewer / "js").rglob("*.js"):
        for ref in re.findall(r'(?:from |import\()\s*"(\.[^"]+\.m?js)"', js.read_text()):
            refs.add(str((js.parent / ref).resolve().relative_to(viewer)))
    refs = {r for r in refs if "://" not in r}  # libraries come from a CDN
    assert refs, "found no references"
    for ref in sorted(refs):
        assert (viewer / ref).is_file(), f"{ref} is referenced but missing"
        assert any(fnmatch(ref, p) for p in patterns), f"{ref} is not in package-data"
