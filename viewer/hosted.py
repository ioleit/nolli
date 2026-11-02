"""nolli view as a service: GitHub's paths, with nolli's host.

    nolli serve [--cache DIR] [--keep SIZE] [--host 127.0.0.1] [--port 8730]

``/<owner>/<repo>/pull/<n>`` shows what ``nolli view https://github.com/<owner>/<repo>/pull/<n>``
shows, ``/<owner>/<repo>/commit/<sha>`` the change that commit makes to its parent, and
``/<owner>/<repo>`` the repository as a whole at the head of its default branch. The first
visit builds the model in the background (a page says how far it got); later visits find it
in the store, keyed by its commits (nolli/store.py), so a new push is a new model.

Models are kept in a ModelStore (default: directories under ``<cache>/data``), and commits
come from a Sources (default: bare clones under ``<cache>/repos``, fetched with the same token
as GitHub's API). git runs with none of this machine's credentials or configuration. GitHub's
API is asked with ``NOLLI_GITHUB_TOKEN`` if it is set (5,000 requests an hour instead of 60),
else as nobody. The server shows whatever that token can read: started with a token that
reaches private repositories, it shows them to anyone who can reach the server. Deciding who
may see what is a store's (and sources') job: one that refuses raises NotAllowed, and the
page answers 404 as for a missing repository.

A service built on this one passes make_server() (and main()) parts of its own: a Token (one
per repository, say a GitHub App's installation token for each installation), a store and
sources (which may decide per request who may read what), a subclass of HostedHandler for
pages and routes of its own, a subclass of HostedServices for what those need, and a reviewer
for "Review structural changes" (nolli's agent, nolli.reviewer.Reviewer, by default). A build
runs in the context (contextvars) of the request that started it, so a store that checks who
asks sees that request.

Each commit's model is kept too, so a pull request's base is extracted once for all its
pushes. With ``--keep 20G`` the default store and clones stay within that size: after each
build, what was used longest ago is deleted (nolli/retention.py).

The steps of a build that take time (extracting a commit, merging two) run in processes of
their own, ``--builds`` builds at the same time (the rest wait), so a server with more CPUs
builds more at once. A step running past ``--timeout`` is stopped, and a repository GitHub
says is larger than ``--max-repo`` is not built at all: anyone can ask for any public
repository.

The viewer is the local one (``serve.py``), its page pinned to the one model; the API is the
same, without ``/api/list`` and without storing uploaded verdicts.

Verdicts come from two places. A pull request's page looks for what nolli's CI left in the
PR's comments for its head commit (nolli/ci.py) and shows those verdicts, asking GitHub at
most once a minute per model. And with ``ANTHROPIC_API_KEY`` set where the server runs,
the viewer's "Review structural changes" starts a review agent (viewer/reviews.py) whose
verdicts then are the model's; anyone who can open the page can start one, so the key's
owner pays for every visitor's review (one run per model at a time, two runs at once).
"""

from __future__ import annotations

import argparse
import contextvars
import html
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE.parent))  # run as a script: the nolli package of this checkout

from nolli import github, retention  # noqa: E402
from nolli.build import fetch_change_model, fetch_whole_model, is_fresh  # noqa: E402
from nolli.change import ChangeError, merge_base  # noqa: E402
from nolli.model import Model  # noqa: E402
from nolli.progress import Progress  # noqa: E402
from nolli.store import (  # noqa: E402
    Change, CloneCache, Commit, DirectoryStore, FetchError, ModelKey, ModelStore, NotAllowed, Repo, Sources,
)

try:  # installed: nolli.viewer.hosted next to nolli.viewer.serve; from a checkout, serve.py beside this file
    from .reviews import Reviews, ci_pull, from_ci
    from .serve import Handler, Server, Services, preload
except ImportError:
    from reviews import Reviews, ci_pull, from_ci  # type: ignore[no-redef]
    from serve import Handler, Server, Services, preload  # type: ignore[no-redef]

OWNER = r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))"
REPO = r"(?P<repo>[A-Za-z0-9._-]{1,100})"
ROUTE = re.compile(rf"^/{OWNER}/{REPO}(?:/(?:(?P<pull>pulls?)/(?P<n>[0-9]{{1,9}})|commit/(?P<sha>[0-9a-fA-F]{{7,40}}))(?:/[a-z]*)?)?/?$")
LOOKUP_TTL = 60  # s: how long an answer from GitHub's API is reused
BUILDS = min(4, os.cpu_count() or 1)  # builds at the same time; the rest wait their turn
TIMEOUT = 600  # s: a step of a build taking longer is stopped
MAX_REPO = 1 << 30  # bytes: GitHub's size of a repository (all its history) above which it is not built
KINDS = {"model": "data/*", "clone": "repos/*/*"}  # what retention may delete (the default store and sources)
OLD = ("extractions",)  # what nolli before 0.7 kept under the cache: deleted at the start


class Token:
    """Which token GitHub's API is asked with about a repository; None asks as nobody (60 requests
    an hour for the whole server). token() may raise ChangeError (or ValueError, ImportError) when
    it cannot make one: the server then says so once and asks as nobody."""

    def token(self, owner: str, repo: str) -> str | None:
        return None

    def describe(self) -> str:
        """For the startup line: what GitHub access the server has."""
        return "no GitHub token: 60 API requests an hour"


class Fixed(Token):
    """One token for every repository."""

    def __init__(self, value: str):
        self.value = value

    def token(self, owner: str, repo: str) -> str | None:
        return self.value

    def describe(self) -> str:
        return "GitHub token from NOLLI_GITHUB_TOKEN"


def token_from_env(env: dict | None = None) -> Token:
    """``NOLLI_GITHUB_TOKEN`` if it is set, else none."""
    value = (os.environ if env is None else env).get("NOLLI_GITHUB_TOKEN")
    return Fixed(value) if value else Token()


@dataclass
class Plan:
    """What a path shows: a build's name (what its page follows), a title, the repository, and
    what to build: a key known before building (a commit, the change a commit makes), or for a
    pull request its two commits, whose key needs the commits (where the PR left its base)."""
    name: str
    title: str
    repo: Repo
    key: ModelKey | None = None
    pull: tuple[str, str] | None = None  # a pull request's base (the tip of its branch) and head
    about: dict = field(default_factory=dict)  # what the model says about the change: pr, title, description


class TooLarge(Exception):
    pass


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
    key: ModelKey | None = None  # what it found or built
    t0: float = field(default_factory=time.time)
    ended: float = 0.0

    def status(self) -> dict:
        return {"state": self.state, "lines": self.board.lines, "current": self.board.current, "error": self.error,
                "seconds": round((self.ended or time.time()) - self.t0)}


class Pipe:
    """A Progress stream in a build step's process: what Progress writes goes to the server's Board."""

    def __init__(self, conn):
        self.conn = conn

    def write(self, s: str) -> int:
        self.conn.send(("out", s))
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


def child(fn: Callable[..., Any], args: tuple, prefix: str, tmp: Path, conn) -> None:
    """A build step's process: ``fn(*args, progress=…)``, then its result or why it failed.
    Temporary files go under ``tmp``, which the server deletes, also when it stops the step."""
    tempfile.tempdir = str(tmp)
    try:
        out = fn(*args, progress=Progress(Pipe(conn), prefix))  # type: ignore[arg-type]
        conn.send(("done", out))
    except BaseException as exc:  # noqa: BLE001  # reported to the server, which shows it on the page
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def processes() -> multiprocessing.context.BaseContext:
    """How build steps' processes start: from a server process that has nolli loaded already, where there is one."""
    if "forkserver" in multiprocessing.get_all_start_methods():
        ctx = multiprocessing.get_context("forkserver")
        ctx.set_forkserver_preload(["nolli.build"])
        return ctx
    return multiprocessing.get_context("spawn")


class Site:
    """The models a server shows, where their commits come from, and the builds under way."""

    def __init__(self, cache: Path, budget: int = 34, keep: int | None = None, builds: int = BUILDS, timeout: float = TIMEOUT,
                 max_repo: int | None = MAX_REPO, token: Token | None = None, store: ModelStore | None = None,
                 sources: Sources | None = None):
        """``store`` and ``sources`` default to directories under ``cache``."""
        self.cache, self.budget = cache, budget
        self.builds, self.timeout, self.max_repo = threading.BoundedSemaphore(builds), timeout, max_repo
        self.processes = processes()
        self.github = token or Token()
        self.token_failed = ""  # why the token could not be had, said once
        for old in OLD:
            shutil.rmtree(cache / old, ignore_errors=True)
        self.store: ModelStore = store or DirectoryStore(cache / "data")
        self.sources: Sources = sources or CloneCache(cache / "repos", self.find_token)
        self.policy: retention.Policy | None = retention.LeastRecentlyUsed(keep) if keep is not None else None
        self.tidying = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.built: dict[str, ModelKey] = {}  # a build's name -> the model it found or built
        self.lock = threading.Lock()
        self.answers: dict[str, tuple[float, dict]] = {}
        self.ci: dict[str, tuple[float, dict]] = {}  # model name -> when its PR's comments were read, what they had

    def find_token(self, owner: str, repo: str) -> str | None:
        """The token for ``owner/repo``, or None to go as nobody (a token that cannot be had is said once)."""
        try:
            return self.github.token(owner, repo)
        except (ChangeError, ValueError, ImportError) as exc:
            if str(exc) != self.token_failed:
                self.token_failed = str(exc)
                print(f"nolli serve: no GitHub token, asking as nobody: {exc}", file=sys.stderr, flush=True)
            return None

    def api(self, owner: str, repo: str, rest: str = "") -> dict:
        """GitHub's answer for ``repos/<owner>/<repo><rest>``, reused for LOOKUP_TTL seconds."""
        path = f"repos/{owner}/{repo}{rest}"
        hit = self.answers.get(path)
        if hit and time.time() - hit[0] < LOOKUP_TTL:
            return hit[1]
        out = self.ask(owner, repo, rest)
        self.answers[path] = (time.time(), out)
        return out

    def ask(self, owner: str, repo: str, rest: str = "") -> dict:
        """GitHub's answer for ``repos/<owner>/<repo><rest>``, with the token for that repository if
        there is one. A token may not see a public repository (an installation token of an app not
        installed there): a 404 with it is asked again as nobody."""
        path = f"repos/{owner}/{repo}{rest}"
        tok = self.find_token(owner, repo)
        try:
            return github.api(path, tok)
        except github.NotFound:
            if not tok:
                raise
            return github.api(path, None)

    def plan(self, owner: str, repo: str, pull: int | None = None, sha: str | None = None) -> Plan:
        """What ``/<owner>/<repo>[/pull/<n> | /commit/<sha>]`` shows. Raises github.NotFound for a
        repository the token cannot read, ChangeError if GitHub cannot be asked."""
        info = self.api(owner, repo)
        owner, repo = info["full_name"].split("/")  # GitHub's spelling (paths are case-insensitive)
        r, name = Repo(f"github.com/{owner}/{repo}"), f"{owner}_{repo}"
        if self.max_repo is not None and info.get("size", 0) * 1024 > self.max_repo:
            raise TooLarge(f"{owner}/{repo} is {info['size'] >> 10} MB on GitHub; this host builds repositories up to {self.max_repo >> 20} MB")
        if pull is not None:
            pr = self.api(owner, repo, f"/pulls/{pull}")
            head, base = pr["head"]["sha"], pr["base"]["sha"]
            return Plan(f"{name}_pr{pull}_{base[:7]}_{head[:7]}", f"{owner}/{repo}#{pull}: {pr.get('title') or ''}", r,
                        pull=(base, head), about={"pr": pull, "title": pr.get("title") or f"PR #{pull}",
                                                  "description": (pr.get("body") or "").replace("\r\n", "\n").strip()})
        if sha is not None:
            c = self.api(owner, repo, f"/commits/{sha}")
            full, parents = c["sha"], [p["sha"] for p in c.get("parents", [])]
            subject, _, body = (c.get("commit", {}).get("message") or "").partition("\n")
            key: ModelKey = r.make_change(parents[0], full) if parents else r.make_commit(full)
            return Plan(f"{name}_c{full[:7]}", f"{owner}/{repo}@{full[:7]}: {subject}", r, key=key,
                        about={"title": subject, "description": body.strip()})
        branch = info["default_branch"]
        head = self.api(owner, repo, f"/branches/{quote(branch, safe='')}")["commit"]["sha"]
        return Plan(f"{name}_{head[:7]}", f"{owner}/{repo} at {branch}", r, key=r.make_commit(head))

    def find_built(self, plan: Plan) -> tuple[ModelKey, Model] | None:
        """The model ``plan`` shows if it is built, else None. Raises NotAllowed as the store does."""
        key = plan.key or self.built.get(plan.name)
        if key is None:
            return None
        m = self.store.load_model(key)
        return (key, m) if m is not None and is_fresh(m) else None

    def ci_review(self, key: ModelKey, m: Model) -> dict:
        """What nolli's CI left for a pull request model's head commit (reviews.from_ci), from the
        PR's comments, read at most once per LOOKUP_TTL."""
        name = key.format_name()
        hit = self.ci.get(name)
        if hit and time.time() - hit[0] < LOOKUP_TTL:
            return hit[1]
        pull = ci_pull(m)
        if pull is None:
            return {"found": None, "applies": False}
        owner, repo, n = pull
        try:
            info = from_ci(self.store, key, m, self.ask(owner, repo, f"/issues/{n}/comments?per_page=100"))
        except ChangeError as exc:
            info = {"found": None, "applies": True, "error": str(exc)}
        self.ci[name] = (time.time(), info)
        return info

    def start(self, plan: Plan) -> Job:
        """The build of ``plan``: the one under way, or a new one (also after a failed one). It
        runs in the context of the request that started it."""
        with self.lock:
            job = self.jobs.get(plan.name)
            if job and job.state != "error":
                return job
            job = self.jobs[plan.name] = Job(plan)
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(self.run_job, job), daemon=True).start()
        return job

    def run_job(self, job: Job) -> None:
        plan, progress = job.plan, Progress(job.board)  # type: ignore[arg-type]
        with self.builds:
            job.state, job.t0 = "building", time.time()
            try:
                key = plan.key
                if key is None and plan.pull is not None:  # a pull request: from where it left its base
                    base_tip, head = plan.pull
                    with progress.step("fetching the pull request"), self.sources.open_commits(plan.repo, base_tip, head) as clone:
                        key = plan.repo.make_change(merge_base(clone, base_tip, head), head)
                if isinstance(key, Change):
                    fetch_change_model(self.store, self.sources, key, about={**plan.about, "budget": self.budget},
                                       run=self.run_step, progress=progress)
                elif isinstance(key, Commit):
                    fetch_whole_model(self.store, self.sources, key, budget=self.budget, run=self.run_step, progress=progress)
                job.key = key
                if key is not None:
                    self.built[plan.name] = key
                job.state = "done"
            except Exception as exc:  # noqa: BLE001  # shown on the page; the server goes on
                job.state, job.error = "error", str(exc) if isinstance(exc, (ChangeError, FetchError)) else f"{type(exc).__name__}: {exc}"
            finally:
                job.ended = time.time()
        self.tidy()

    def run_step(self, fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
        """Run a build's step ``fn(*args, progress=…)`` in a process of its own, its progress on
        ``progress``; its result. A step running past the timeout is stopped (ChangeError)."""
        (self.cache / ".tmp").mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="step-", dir=self.cache / ".tmp"))
        recv, send = self.processes.Pipe(duplex=False)
        proc = self.processes.Process(target=child, args=(fn, args, progress.prefix, tmp, send), daemon=True)
        proc.start()
        send.close()
        deadline, ended = time.time() + self.timeout, None
        try:
            while ended is None:
                left = deadline - time.time()
                if left <= 0:
                    ended = ("error", f"stopped after {self.timeout:.0f} s: too big a change for this host")
                elif recv.poll(min(left, 1.0)):
                    try:
                        kind, value = recv.recv()
                    except EOFError:  # the process ended without a word: killed (out of memory?)
                        proc.join(5)
                        ended = ("error", f"the build's process ended (exit code {proc.exitcode})")
                        continue
                    if kind == "out":
                        progress.stream.write(value)
                    else:
                        ended = (kind, value)
        finally:
            recv.close()
            if proc.is_alive():
                proc.join(5 if ended and ended[0] == "done" else 0)
            if proc.is_alive():
                proc.kill()
                proc.join()
            shutil.rmtree(tmp, ignore_errors=True)
        if ended[0] != "done":
            raise ChangeError(ended[1])
        return ended[1]

    def tidy(self) -> list[retention.Entry]:
        """Keep the default store and clones within their size (--keep): delete what was used
        longest ago, except clones in use and the models of builds under way. Runs after each
        build, the only thing that adds to them."""
        if not self.policy or not self.tidying.acquire(blocking=False):
            return []
        try:
            chosen = self.policy(retention.scan(self.cache, KINDS))
            with self.lock:  # no build starts while what it uses may be going
                active = [j for j in self.jobs.values() if j.state in ("waiting", "building")]
                busy = {self.cache / "data" / k.format_name() for j in active if (k := j.plan.key or j.key)}
                if isinstance(self.sources, CloneCache):
                    busy |= self.sources.list_held()
                deleted = [e for e in chosen if e.path not in busy and retention.delete(self.cache, KINDS, e)]
            for e in deleted:
                print(f"nolli serve: deleted {e.kind} {e.path.relative_to(self.cache)} ({e.size >> 20} MB, used {retention.ago(e.used)} ago)",
                      file=sys.stderr, flush=True)
            return deleted
        finally:
            self.tidying.release()


def page(title: str, body: str, *, status: str = "", job: str = "") -> bytes:
    """hosted.html with a title and body (HTML), and the build it follows, if any."""
    t = (HERE / "hosted.html").read_text()
    return (t.replace("{{title}}", html.escape(title)).replace("{{body}}", body)
            .replace("{{job}}", html.escape(job, quote=True)).replace("{{status}}", status)).encode()


def viewer_page(name: str, title: str) -> bytes:
    """The viewer's page, pinned to one model; <base> makes its relative links work under any path."""
    t = (HERE / "index.html").read_text()
    head = f'<head>\n<base href="/">\n<meta name="nolli-dataset" content="{html.escape(name, quote=True)}">'
    return t.replace("<head>", head, 1).replace("<title>nolli</title>", f"<title>{html.escape(title)} · nolli</title>", 1).encode()


@dataclass
class HostedServices(Services):
    """A hosted server's parts: those of any viewer server, and the site."""

    site: Site | None = None


class HostedHandler(Handler):
    @property
    def site(self) -> Site:
        return self.services.site  # type: ignore[attr-defined, return-value]

    def review_status(self, name: str, key: ModelKey, m: Model) -> dict:
        return {**super().review_status(name, key, m), "ci": self.site.ci_review(key, m), "server": "serve"}

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
            self.send_json({"error": "not here: a page shows the model of its path"}, 404)
            return
        if path == "/api/job":
            name = unquote(self.path.partition("?d=")[2])
            job = self.site.jobs.get(name)
            self.send_json(job.status() if job else {"state": "done" if name in self.site.built else "unknown"})
            return
        if path.startswith("/api/") or self.static(path):
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
        what = f"{owner}/{repo}" + (f" #{m.group('n')}" if m.group("n") else f" @ {m.group('sha')}" if m.group("sha") else "")
        try:
            plan = self.site.plan(owner, repo, int(m.group("n")) if m.group("n") else None, m.group("sha"))
            found = self.site.find_built(plan)
        except (github.NotFound, NotAllowed):
            self.send_page(page("Not found, or private", PRIVATE.format(what=html.escape(what))), 404)
            return
        except TooLarge as exc:
            self.send_page(page("Too large for this host", f"<h1>Too large for this host</h1><p>{html.escape(str(exc))}.</p>"), 413)
            return
        except (ChangeError, KeyError) as exc:
            self.send_page(page("GitHub did not answer", f"<p>{html.escape(str(exc))}</p>"), 502)
            return
        if found is not None:
            key, model = found
            name = key.format_name()
            preload(self.site.store, name)
            if (model.meta.get("diff") or {}).get("pr"):
                self.site.ci_review(key, model)  # before the page: a review found opens on its worst verdict
            self.send_page(viewer_page(name, plan.title))
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
<p>This host cannot read <code>{what}</code> on GitHub: it does not exist, or it is private
and this host may not show it to you.</p>"""

NOT_A_PAGE = """<h1>Not a nolli page</h1>
<p>Paths here are GitHub's: <code>/&lt;owner&gt;/&lt;repo&gt;</code>, <code>…/pull/&lt;n&gt;</code>,
<code>…/commit/&lt;sha&gt;</code>. <a href="/">Start here</a>.</p>"""


def make_server(cache: Path, host: str = "127.0.0.1", port: int = 8730, budget: int = 34, keep: int | None = None,
                builds: int = BUILDS, timeout: float = TIMEOUT, max_repo: int | None = MAX_REPO, token: Token | None = None,
                handler: type[HostedHandler] | None = None, reviewer=None, store: ModelStore | None = None,
                sources: Sources | None = None, services: type[HostedServices] = HostedServices, **more: Any) -> Server:
    """A server for ``cache``, not yet serving. GitHub's API is asked with ``token`` (default:
    token_from_env()). ``handler``: a subclass of HostedHandler, for a service's own routes;
    ``reviewer``: what runs a review (default: nolli.reviewer.Reviewer); ``store`` and
    ``sources``: where models are kept and commits come from (default: directories under
    ``cache``); ``services``: a subclass of HostedServices, given ``more`` of its fields."""
    # git: no credentials, prompts, or this machine's configuration (an insteadOf could reach private repos by ssh)
    os.environ.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ASKPASS="true",
                      SSH_ASKPASS="true", GIT_SSH_COMMAND="false")
    cache = Path(cache).expanduser().resolve()
    site = Site(cache, budget, keep, builds, timeout, max_repo, token if token is not None else token_from_env(), store, sources)
    threading.Thread(target=site.tidy, daemon=True).start()  # a smaller --keep than last time takes effect now
    svc = services(site.store, Reviews(reviewer, cache / ".reviews"), sources=site.sources, uploads=False, site=site, **more)
    return Server((host, port), partial(handler or HostedHandler, directory=str(HERE)), svc)


def main(argv: list[str] | None = None, token: Token | None = None, handler: type[HostedHandler] | None = None,
         reviewer=None, store: ModelStore | None = None, sources: Sources | None = None,
         services: type[HostedServices] = HostedServices, **more: Any) -> int:
    """``nolli serve``'s options from ``argv``; the rest as make_server() takes them."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=os.environ.get("NOLLI_CACHE") or str(Path.home() / ".cache" / "nolli-serve"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8730))
    ap.add_argument("--budget", type=int, default=34)
    ap.add_argument("--keep", type=retention.parse_size, default=os.environ.get("NOLLI_CACHE_KEEP"),
                    help="the cache's size at most, e.g. 20G: what was used longest ago goes first (default: keep everything)")
    ap.add_argument("--builds", type=int, default=int(os.environ.get("NOLLI_BUILDS") or BUILDS),
                    help=f"builds at the same time (default: $NOLLI_BUILDS, else {BUILDS}: the CPUs, at most 4)")
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("NOLLI_BUILD_TIMEOUT") or TIMEOUT),
                    help=f"seconds a step of a build may take before it is stopped (default: $NOLLI_BUILD_TIMEOUT, else {TIMEOUT})")
    ap.add_argument("--max-repo", type=retention.parse_size, default=os.environ.get("NOLLI_MAX_REPO") or str(MAX_REPO),
                    help="the largest repository built, by GitHub's size of it, e.g. 500M; 0 for no limit (default: $NOLLI_MAX_REPO, else 1G)")
    args = ap.parse_args(argv)
    srv = make_server(Path(args.cache), args.host, args.port, args.budget, args.keep, args.builds, args.timeout,
                      args.max_repo or None, token, handler, reviewer, store, sources, services, **more)
    site = srv.services.site  # type: ignore[attr-defined]
    print(f"nolli serve: http://{args.host}:{srv.server_address[1]}/  (cache {site.cache}; {site.github.describe()})",
          file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
