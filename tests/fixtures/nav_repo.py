"""A made-up change for tests of unfolding and folding: in "tasks", a small app, almost
every module uses ``core/log.py`` and ``core/clock.py``, so the viewer draws both as
utilities (at the bottom, their incoming edges as badges). The branch ``log-times`` makes
``clock`` import ``log``: a changed edge between two utilities, which a review keeps drawn.

    python tests/fixtures/nav_repo.py <out_dir>

builds the repository in <out_dir>/tasks and the change's dataset in <out_dir>/data/tasks,
and prints the dataset's path.
"""

from __future__ import annotations

import sys
from pathlib import Path

from review_repo import git, write

DATASET = "tasks"

AREAS = ["api", "jobs", "store", "notify", "billing", "reports", "search"]
USERS = ["api/routes", "api/auth", "jobs/scheduler", "jobs/worker", "store/tasks", "store/users", "notify/mail",
         "billing/invoices", "reports/daily", "search/index"]

BASE = {
    "tasks/__init__.py": "",
    "tasks/settings.py": "import os\n\nLEVEL = os.environ.get('LOG_LEVEL', 'INFO')\n",
    "tasks/core/__init__.py": "",
    "tasks/core/log.py": "from tasks.settings import LEVEL\n\n\ndef log(msg):\n    print(LEVEL, msg)\n",
    "tasks/core/clock.py": "import time\n\n\ndef now():\n    return time.time()\n",
    **{f"tasks/{a}/__init__.py": "" for a in AREAS},
    **{f"tasks/{u}.py": f"from tasks.core.clock import now\nfrom tasks.core.log import log\n\n\ndef run():\n    log(('{u}', now()))\n"
       for u in USERS},
}
# a few edges between the areas, so they are not just a row of boxes
BASE["tasks/api/routes.py"] += "\nfrom tasks.store import tasks as store\n"
BASE["tasks/jobs/worker.py"] += "\nfrom tasks.store import tasks as store\nfrom tasks.notify import mail\n"
BASE["tasks/api/auth.py"] += "\nfrom tasks.store import users\n"

HEAD = {"tasks/core/clock.py": "import time\n\nfrom tasks.core.log import log\n\n\ndef now():\n    t = time.time()\n    log(t)\n    return t\n"}


def build(out: Path) -> Path:
    """The repository under out/tasks, the change's dataset under out/data; returns the dataset."""
    from nolli.cli import main

    repo, ds = out / "tasks", out / "data" / DATASET
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "dev@example.com")
    git(repo, "config", "user.name", "dev")
    write(repo, BASE)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tasks")
    git(repo, "checkout", "-q", "-b", "log-times")
    write(repo, HEAD)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Log every clock reading")
    if main(["diff", str(repo), "main", "log-times", "-o", str(ds), "--title", "Log every clock reading"]) != 0:
        raise RuntimeError("nolli diff failed")
    return ds


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
