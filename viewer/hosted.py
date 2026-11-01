"""nolli view as a service: GitHub's paths, with nolli's host.

    nolli serve [--cache DIR] [--keep SIZE] [--host 127.0.0.1] [--port 8730]

``/<owner>/<repo>/pull/<n>`` shows what ``nolli view https://github.com/<owner>/<repo>/pull/<n>``
shows, ``/<owner>/<repo>/commit/<sha>`` the change that commit makes to its parent, and
``/<owner>/<repo>`` the repository as a whole at the head of its default branch. The first
visit builds the dataset in the background (a page says how far it got); later visits find
it under ``<cache>/data``, named after the commits, so a new push is a new dataset.

Repositories are kept as bare clones under ``<cache>/repos``. Everything is asked for as
nobody: GitHub's API without a token and git with no credentials and none of this machine's
git configuration, so only public repositories can be shown, whatever this machine's own
logins could reach. A private repository answers 404 like a missing one.

Each commit's extraction is kept under ``<cache>/extractions`` (nolli/extractions.py), so a pull
request's base is extracted once for all its pushes. With ``--keep 20G`` the cache stays within
that size: after each build, what was used longest ago is deleted (nolli/retention.py).

The viewer is the local one (``serve.py``), its page pinned to the one dataset; the API is the
same, without ``/api/list`` and without storing uploaded verdicts.

Verdicts come from two places. A pull request's page looks for what nolli's CI left in the
PR's comments for its head commit (nolli/ci.py) and shows those verdicts, asking GitHub at
most once a minute per dataset. And with ``ANTHROPIC_API_KEY`` set where the server runs,
the viewer's "Review structural changes" starts a review agent (viewer/reviews.py) whose
verdicts then are the dataset's; anyone who can open the page can start one, so the key's
owner pays for every visitor's review (one run per dataset at a time, two runs at once).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from urllib.parse import quote, unquote

HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE.parent))  # run as a script: the nolli package of this checkout

from nolli import github, retention  # noqa: E402
from nolli.change import ChangeError, git  # noqa: E402
from nolli.build import build_commit, build_diff, fresh, version  # noqa: E402
from nolli.extractions import Extractions  # noqa: E402
from nolli.progress import Progress  # noqa: E402

try:  # installed: nolli.viewer.hosted next to nolli.viewer.serve; from a checkout, serve.py beside this file
    from .serve import Handler, preload
    from .reviews import provenance, store
except ImportError:
    from serve import Handler, preload  # type: ignore[no-redef]
    from reviews import provenance, store  # type: ignore[no-redef]

OWNER = r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))"
REPO = r"(?P<repo>[A-Za-z0-9._-]{1,100})"
ROUTE = re.compile(rf"^/{OWNER}/{REPO}(?:/(?:(?P<pull>pulls?)/(?P<n>[0-9]{{1,9}})|commit/(?P<sha>[0-9a-fA-F]{{7,40}}))(?:/[a-z]*)?)?/?$")
LOOKUP_TTL = 60  # s: how long an answer from GitHub's API is reused
BUILDS = threading.BoundedSemaphore(2)  # builds at the same time; the rest wait their turn
KINDS = {"dataset": "data/*", "clone": "repos/*/*", "extraction": "extractions/*.json"}  # what retention may delete


@dataclass
class Plan:
    """What a path shows: the dataset's name, a title, and how to build it in a clone."""
    name: str
    title: str
    remote: str
    build: Callable[[Path, Path, Progress], None]


class Board:
    """A Progress stream for a page: the finished steps' lines, and the step under way."""

    def __init__(self):
        self.lines: list[str] = []
        self.current = ""

    def write(self, s: str) -> int:
        if s.startswith("\r\x1b[K"):  # the line under way, rewritten (Progress on a terminal)
            self.current = s[4:].strip()
        else:
            self.lines += [line.strip() for line in s.splitlines() if line.strip()]
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


@dataclass
class Job:
    plan: Plan
    board: Board = field(default_factory=Board)
    state: str = "waiting"  # waiting, building, done, error
    error: str = ""
    t0: float = field(default_factory=time.time)
    ended: float = 0.0

    def status(self) -> dict:
        return {"state": self.state, "lines": self.board.lines, "current": self.board.current, "error": self.error,
                "seconds": round((self.ended or time.time()) - self.t0)}


class Site:
    """The datasets and clones under one cache directory, and the builds under way."""

    def __init__(self, cache: Path, budget: int = 34, keep: int | None = None):
        self.cache, self.budget = cache, budget
        self.data, self.repos = cache / "data", cache / "repos"
        self.data.mkdir(parents=True, exist_ok=True)
        self.repos.mkdir(parents=True, exist_ok=True)
        self.extractions = Extractions(cache / "extractions", version())
        self.policy: retention.Policy | None = retention.LeastRecentlyUsed(keep) if keep is not None else None
        self.tidying = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.repo_locks: dict[str, threading.Lock] = {}
        self.answers: dict[str, tuple[float, dict]] = {}
        self.ci: dict[str, tuple[float, dict]] = {}  # dataset name -> when its PR's comments were read, what they had

    def api(self, path: str) -> dict:
        """GitHub's answer for ``path``, asked as nobody, reused for LOOKUP_TTL seconds."""
        hit = self.answers.get(path)
        if hit and time.time() - hit[0] < LOOKUP_TTL:
            return hit[1]
        out = github.api(path, None)
        self.answers[path] = (time.time(), out)
        return out

    def plan(self, owner: str, repo: str, pull: int | None = None, sha: str | None = None) -> Plan:
        """What ``/<owner>/<repo>[/pull/<n> | /commit/<sha>]`` shows. Raises github.NotFound for a
        missing or private repository, ChangeError if GitHub cannot be asked."""
        info = self.api(f"repos/{owner}/{repo}")
        owner, repo = info["full_name"].split("/")  # GitHub's spelling (paths are case-insensitive)
        remote, key = f"https://github.com/{owner}/{repo}", f"{owner}_{repo}"
        if pull is not None:
            pr = self.api(f"repos/{owner}/{repo}/pulls/{pull}")
            head, base = pr["head"]["sha"], pr["base"]["sha"]

            def build_pull(clone: Path, out: Path, progress: Progress) -> None:
                with progress.step("fetching the pull request"), self.repo_lock(clone):
                    ch, host = github.resolve(clone, f"{remote}/pull/{pull}", None)
                build_diff(clone, ch.base, ch.head, out, self.budget, pr=pull, title=ch.title, description=ch.description or None,
                           remote=host["remote"], progress=progress, extractions=self.extractions)
                (out / "pr.md").write_text(ch.description)

            return Plan(f"{key}_pr{pull}_{base[:7]}_{head[:7]}", f"{owner}/{repo}#{pull}: {pr.get('title') or ''}", remote, build_pull)
        if sha is not None:
            c = self.api(f"repos/{owner}/{repo}/commits/{sha}")
            full, parents = c["sha"], [p["sha"] for p in c.get("parents", [])]
            subject, _, body = (c.get("commit", {}).get("message") or "").partition("\n")

            def build_one(clone: Path, out: Path, progress: Progress) -> None:
                with progress.step("fetching the commit"), self.repo_lock(clone):
                    git(clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", full, *parents[:1])
                if parents:
                    build_diff(clone, parents[0], full, out, self.budget, title=subject, description=body.strip() or None,
                               remote=remote, progress=progress, extractions=self.extractions)
                    (out / "pr.md").write_text(body.strip())
                else:
                    build_commit(clone, full, out, self.budget, progress, self.extractions)

            return Plan(f"{key}_c{full[:7]}", f"{owner}/{repo}@{full[:7]}: {subject}", remote, build_one)
        branch = info["default_branch"]
        head = self.api(f"repos/{owner}/{repo}/branches/{quote(branch, safe='')}")["commit"]["sha"]

        def build_whole(clone: Path, out: Path, progress: Progress) -> None:
            with progress.step("fetching the repository"), self.repo_lock(clone):
                git(clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", head)
            build_commit(clone, head, out, self.budget, progress, self.extractions)

        return Plan(f"{key}_{head[:7]}", f"{owner}/{repo} at {branch}", remote, build_whole)

    def ci_review(self, ds: Path) -> dict:
        """What nolli's CI left for a pull request dataset's head commit, read from the PR's comments
        (at most once per LOOKUP_TTL): ``{"found": "verdicts" | "no-review" | "earlier" | None, ...}``.
        Verdicts found are stored as the dataset's, unless it has some already."""
        from nolli.ci import results, upgrade
        from nolli.model import Model
        from nolli.review import check_verdicts

        hit = self.ci.get(ds.name)
        if hit and time.time() - hit[0] < LOOKUP_TTL:
            return hit[1]
        try:
            d = json.loads((ds / "report.json").read_text())["meta"].get("diff") or {}
        except (OSError, ValueError, KeyError):
            d = {}
        m = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", d.get("remote") or "")
        if not m or not d.get("pr"):
            return {"found": None, "applies": False}
        info: dict = {"found": None, "applies": True}
        try:
            comments = github.api(f"repos/{m.group(1)}/{m.group(2)}/issues/{d['pr']}/comments?per_page=100", None)
        except ChangeError as exc:
            info["error"] = str(exc)
            self.ci[ds.name] = (time.time(), info)
            return info
        found = results(comments if isinstance(comments, list) else [])
        mine = [r for r in found if r["head"] == d["head"]]
        if mine:
            r = mine[0]
            info.update(link=r["link"], at=r["at"], nolli=r.get("nolli"))
            if not r.get("verdicts"):
                info["found"] = "no-review"
            else:
                info["found"] = "verdicts"
                if provenance(ds) is None:
                    model = Model.load(ds / "model.json")
                    given = upgrade(r["verdicts"], r.get("nolli"))
                    ok, errors = check_verdicts(model, (ds / "review.mmd").read_text(), json.dumps(given))
                    info["dropped"] = len(errors)
                    if ok:
                        store(ds, ok, {"by": "ci", "link": r["link"], "at": r["at"], "nolli": r.get("nolli"),
                                       "dropped": len(r["verdicts"]) - len(ok)})
        elif found:
            info.update(found="earlier", head=found[0]["head"][:7], link=found[0]["link"])
        self.ci[ds.name] = (time.time(), info)
        return info

    def review_clone(self, ds: Path) -> Path:
        """The clone a review agent reads a dataset's change from, with its base and head fetched
        (retention may have deleted it since the build)."""
        d = json.loads((ds / "report.json").read_text())["meta"]["diff"]
        clone = self.clone(d["remote"])
        with self.repo_lock(clone):
            missing = [c for c in (d["base"], d["head"]) if subprocess.run(
                ["git", "-C", str(clone), "cat-file", "-e", f"{c}^{{commit}}"], capture_output=True, check=False).returncode]
            if missing:
                git(clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *missing)
        return clone

    def repo_lock(self, clone: Path) -> threading.Lock:
        with self.lock:
            return self.repo_locks.setdefault(str(clone), threading.Lock())

    def clone_path(self, remote: str) -> Path:
        owner, repo = remote.rsplit("/", 2)[-2:]
        return self.repos / owner.lower() / repo.lower()

    def clone(self, remote: str) -> Path:
        """The bare clone for ``remote`` (made empty on first use; the builds fetch what they need)."""
        path = self.clone_path(remote)
        with self.repo_lock(path):
            if not (path / "HEAD").is_file():
                path.mkdir(parents=True, exist_ok=True)
                git(path, "init", "-q", "--bare")
                git(path, "remote", "add", "origin", remote)
        retention.touch(path)
        return path

    def start(self, plan: Plan) -> Job:
        """The build of ``plan``'s dataset: the one under way, or a new one (also after a failed one)."""
        with self.lock:
            job = self.jobs.get(plan.name)
            if job and job.state != "error":
                return job
            job = self.jobs[plan.name] = Job(plan)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        out, tmp = self.data / job.plan.name, self.data / f".{job.plan.name}.building"
        progress = Progress(job.board)  # type: ignore[arg-type]  # a stream as far as Progress cares
        with BUILDS:
            job.state, job.t0 = "building", time.time()
            try:
                shutil.rmtree(tmp, ignore_errors=True)
                job.plan.build(self.clone(job.plan.remote), tmp, progress)
                shutil.rmtree(out, ignore_errors=True)
                tmp.rename(out)  # whole or not at all: fresh() reads report.json, written before the rest
                job.state = "done"
            except Exception as exc:  # noqa: BLE001  # shown on the page; the server goes on
                job.state, job.error = "error", f"{type(exc).__name__}: {exc}"
                shutil.rmtree(tmp, ignore_errors=True)
            finally:
                job.ended = time.time()
        self.tidy()

    def tidy(self) -> list[retention.Entry]:
        """Keep the cache within its size (--keep): delete what was used longest ago, except what
        a build waiting or under way uses. Runs after each build, the only thing that adds to it."""
        if not self.policy or not self.tidying.acquire(blocking=False):
            return []
        try:
            chosen = self.policy(retention.scan(self.cache, KINDS))
            deleted = []
            with self.lock:  # no build starts while its clone or dataset may be going
                active = [j.plan for j in self.jobs.values() if j.state in ("waiting", "building")]
                busy = {self.data / p.name for p in active} | {self.clone_path(p.remote) for p in active}
                deleted = [e for e in chosen if e.path not in busy and retention.delete(self.cache, KINDS, e)]
            for e in deleted:
                print(f"nolli serve: deleted {e.kind} {e.path.relative_to(self.cache)} ({e.size >> 20} MB, used {retention.ago(e.used)} ago)",
                      file=sys.stderr, flush=True)
            return deleted
        finally:
            self.tidying.release()


def page(title: str, body: str, *, status: str = "", job: str = "") -> bytes:
    """hosted.html with a title and body (HTML), and the dataset whose build it follows, if any."""
    t = (HERE / "hosted.html").read_text()
    return (t.replace("{{title}}", html.escape(title)).replace("{{body}}", body)
            .replace("{{job}}", html.escape(job, quote=True)).replace("{{status}}", status)).encode()


def viewer_page(name: str, title: str) -> bytes:
    """The viewer's page, pinned to one dataset; <base> makes its relative links work under any path."""
    t = (HERE / "index.html").read_text()
    head = f'<head>\n<base href="/">\n<meta name="nolli-dataset" content="{html.escape(name, quote=True)}">'
    return t.replace("<head>", head, 1).replace("<title>nolli</title>", f"<title>{html.escape(title)} · nolli</title>", 1).encode()


class HostedHandler(Handler):
    site: Site
    uploads = False  # a visitor's verdicts would be shown to everyone as the review

    def review_repo(self, ds: Path) -> Path | None:
        try:
            remote = json.loads((ds / "report.json").read_text())["meta"]["diff"]["remote"]
        except (OSError, ValueError, KeyError):
            return None
        return self.site.clone_path(remote)  # fetched when a run starts (review_clone)

    def start_review(self, ds: Path) -> None:
        self.reviews.start(ds.name, ds, lambda: self.site.review_clone(ds))

    def review_status(self, ds: Path) -> dict:
        return {**super().review_status(ds), "ci": self.site.ci_review(ds), "server": "serve"}

    def send_page(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?")[0])
        if path == "/":
            self.send_page(page("nolli", LANDING))
            return
        if path == "/api/list":
            self.send_json({"error": "not here: a page shows the dataset of its path"}, 404)
            return
        if path == "/api/job":
            name = self.path.partition("?d=")[2]
            job = self.site.jobs.get(unquote(name))
            if job:
                self.send_json(job.status())
            else:
                self.send_json({"state": "done" if fresh(self.site.data / unquote(name)) else "unknown"})
            return
        if path.startswith(("/api/", "/data/")) or self.static(path):
            super().do_GET()
            return
        m = ROUTE.match(path)
        if not m:
            self.send_page(page("Not a nolli page", NOT_A_PAGE), 404)
            return
        self.show(m)

    def static(self, path: str) -> bool:
        """A file of the viewer itself (its scripts and styles), not a repository's path."""
        f = (HERE / path.lstrip("/")).resolve()
        return f.is_file() and f.is_relative_to(HERE) and f.suffix in (".js", ".css", ".html", ".svg", ".png")

    def show(self, m: re.Match) -> None:
        owner, repo = m.group("owner"), m.group("repo").removesuffix(".git")
        try:
            plan = self.site.plan(owner, repo, int(m.group("n")) if m.group("n") else None, m.group("sha"))
        except github.NotFound:
            what = f"{owner}/{repo}" + (f" #{m.group('n')}" if m.group("n") else f" @ {m.group('sha')}" if m.group("sha") else "")
            self.send_page(page("Not found, or private", PRIVATE.format(what=html.escape(what))), 404)
            return
        except (ChangeError, KeyError) as exc:
            self.send_page(page("GitHub did not answer", f"<p>{html.escape(str(exc))}</p>"), 502)
            return
        out = self.site.data / plan.name
        if fresh(out):
            retention.touch(out)
            preload(out)
            if m.group("n"):
                self.site.ci_review(out)  # before the page: a review found opens on its worst verdict
            self.send_page(viewer_page(plan.name, plan.title))
            return
        job = self.site.start(plan)
        self.send_page(page(plan.title, BUILDING.format(title=html.escape(plan.title)), job=plan.name,
                            status=html.escape(json.dumps(job.status()), quote=True)))


LANDING = """<h1>nolli</h1>
<p>A pull request drawn as a change to the repository's architecture. Take a link to a public
GitHub repository, pull request or commit, and replace <code>github.com</code> with this host.</p>
<form id="go"><input id="url" placeholder="https://github.com/owner/repo/pull/123" autofocus><button>Show</button></form>
<script>
document.getElementById("go").addEventListener("submit", e => {
  e.preventDefault();
  const v = document.getElementById("url").value.trim().replace(/^(https?:\\/\\/)?(www\\.)?github\\.com/, "");
  location.href = "/" + v.replace(/^\\/+/, "");
});
</script>"""

BUILDING = """<h1>{title}</h1>
<p>Not drawn yet: reading the repository and building the picture. This page shows it when it is ready.</p>
<pre id="log"></pre>"""

PRIVATE = """<h1>Not found, or private</h1>
<p>GitHub has no public <code>{what}</code>. If it is private: signing in with GitHub is not
there yet, so this host shows public repositories only.</p>"""

NOT_A_PAGE = """<h1>Not a nolli page</h1>
<p>Paths here are GitHub's: <code>/&lt;owner&gt;/&lt;repo&gt;</code>, <code>…/pull/&lt;n&gt;</code>,
<code>…/commit/&lt;sha&gt;</code>. <a href="/">Start here</a>.</p>"""


def make_server(cache: Path, host: str = "127.0.0.1", port: int = 8730, budget: int = 34, keep: int | None = None) -> ThreadingHTTPServer:
    """A server for ``cache``, not yet serving. Asks GitHub and git as nobody (module docstring)."""
    # git: no credentials, prompts, or this machine's configuration (an insteadOf could reach private repos by ssh)
    os.environ.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ASKPASS="true",
                      SSH_ASKPASS="true", GIT_SSH_COMMAND="false")
    site = Site(Path(cache).expanduser().resolve(), budget, keep)
    threading.Thread(target=site.tidy, daemon=True).start()  # a smaller --keep than last time takes effect now
    HostedHandler.site = site
    HostedHandler.data_root = site.data
    return ThreadingHTTPServer((host, port), partial(HostedHandler, directory=str(HERE)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=os.environ.get("NOLLI_CACHE") or str(Path.home() / ".cache" / "nolli-serve"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8730))
    ap.add_argument("--budget", type=int, default=34)
    ap.add_argument("--keep", type=retention.parse_size, default=os.environ.get("NOLLI_CACHE_KEEP"),
                    help="the cache's size at most, e.g. 20G: what was used longest ago goes first (default: keep everything)")
    args = ap.parse_args(argv)
    srv = make_server(Path(args.cache), args.host, args.port, args.budget, args.keep)
    print(f"nolli serve: http://{args.host}:{srv.server_address[1]}/  (cache {HostedHandler.site.cache})", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
