"""Test-wide settings: the command line's data directory is a temporary one, so that no test
builds into the user's own (``nolli build``, ``-o``, ``view`` and the agent commands all keep
what they build there)."""

import os
import tempfile


def pytest_configure(config):
    os.environ["NOLLI_DATA"] = tempfile.mkdtemp(prefix="nolli-test-data-")
