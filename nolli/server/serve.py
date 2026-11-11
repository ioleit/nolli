"""The viewer's server, for ``nolli serve`` and ``nolli view``.

    nolli serve [--cache DIR] [--keep SIZE] [--builds N] [--timeout S] [--max-repo SIZE] [--host H] [--port N]
    nolli view [<target>] …     (a server on the data directory and the repository you are in)

A page shows one model, named by a target (``nolli --help`` lists the forms):

- GitHub's paths with this host: ``/<owner>/<repo>``, ``…/tree/<ref>``, ``…/commit/<sha>``,
  ``…/compare/<a>...<b>``, ``…/pull/<n>``;
- ``/?t=<target>``, any target, on a server started on a local repository (``nolli view``): a
  branch, a range, ``WORKTREE``, ``#<n>``, or a dataset directory in the server's store.

A target is resolved on every visit, so a new push or a new edit is a new model. What it names
is built in the background if the store does not have it yet (a page says how far it got, and
shows the model when it is ready), and kept in the store, keyed by its commits (nolli/store/).
Each commit's model is kept too, so a pull request's base is extracted once for all its pushes.

The steps of a build that take time run in processes of their own, ``--builds`` at the same
time (the rest wait). ``nolli serve`` stops a step running past ``--timeout`` and does not
build a repository GitHub says is larger than ``--max-repo``: anyone can ask for any public
repository. With ``--keep 20G`` its store and clones stay within that size: after each build,
what was used longest ago is deleted (nolli/server/retention.py).

The viewer's page asks the API about the model it shows by its key (``k=<key>``,
``ModelKey.format_key()``): ``/api/scene?k=…&…`` the scene a scene state projects
(``parse_spec`` reads the viewer's location-hash keys; nolli/logic/transformations/projection/__init__.py),
as JSON with every overlay (nolli/logic/formats/viewer.py); ``/api/mermaid`` the same scene as
Mermaid (``nodim=1``: unchanged not faded; ``format=json``: ``{mermaid, ids}``);
``/api/changes`` a change's numbered changes and the review's verdicts; ``/api/brief`` what a
review agent gets (Markdown); ``POST /api/verdicts`` stores a review's verdicts (``nolli view``
only); ``/api/review`` says where the verdicts came from and whether a review agent can run
here, ``POST /api/review`` starts one (it needs ANTHROPIC_API_KEY, reviews.py) and ``POST
/api/review/cancel`` stops it. ``/api/job?j=`` is a build's progress.

GitHub's API is asked with a token (``Token``): for ``nolli serve`` ``NOLLI_GITHUB_TOKEN`` if
it is set (5,000 requests an hour instead of 60), else as nobody; ``nolli view`` uses yours
($GH_TOKEN, $GITHUB_TOKEN or gh). ``nolli serve`` shows whatever its token can read, to anyone
who can reach it, and runs git with none of this machine's credentials or configuration.
Deciding who may see what is a store's (and sources') job: one that refuses raises
NotAllowed, and the page answers 404 as for a missing repository.

A service built on this one passes make_server() (and main()) parts of its own: a Token (one
per repository, say a GitHub App's installation token for each installation), a store and
sources (which may decide per request who may read what), a subclass of Handler for pages and
routes of its own, a subclass of Services for what those need, and a reviewer for "Review
structural changes" (nolli.review.agent.Reviewer by default). A build runs in the context
(contextvars) of the request that started it, so a store that checks who asks sees that
request. With ``ANTHROPIC_API_KEY`` set where the server runs, anyone who can open a page can
start a review, so the key's owner pays for every visitor's review (one run per model at a
time, two runs at once).
"""

from __future__ import annotations

import argparse
import contextvars
import dataclasses
import html
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote

import nolli.viewer  # noqa: E402  (the page, its scripts and styles)

HERE = Path(nolli.viewer.__file__).resolve().parent  # the viewer's files

from nolli import build, scenes  # noqa: E402
from nolli.logic.formats import mermaid, viewer  # noqa: E402

from ..logic.sources import CloneCache, FetchError, LocalFirst, Sources, github  # noqa: E402
from ..logic.sources.git import ChangeError, GitError  # noqa: E402
from nolli.logic import transformations  # noqa: E402
from nolli.logic.transformations import merge, projection  # noqa: E402
from nolli.review import brief, changes, verdicts  # noqa: E402

from ..logic.transformations.projection import DEFAULT_UTILITY_IN, ProjectionSpec  # noqa: E402
from ..model.keys import ChangeKey, CommitKey, ModelKey, NotAllowed, Repo  # noqa: E402
from ..model.scene import Scene  # noqa: E402
from ..progress import Progress  # noqa: E402
from ..store import DirectoryStore, ModelStore  # noqa: E402
from . import retention  # noqa: E402
from .reviews import Reviews  # noqa: E402

LOOKUP_TTL = 60  # s: how long an answer from GitHub's API is reused
BUILDS = min(4, os.cpu_count() or 1)  # builds at the same time; the rest wait their turn
TIMEOUT = 600  # s: a step of a build taking longer is stopped (nolli serve)
MAX_REPO = 1 << 30  # bytes: GitHub's size of a repository (all its history) above which it is not built (nolli serve)
KINDS = {"model": "data/*", "clone": "repos/*/*"}  # what retention may delete (nolli serve's store and clones)
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


class Yours(Token):
    """The token of whoever runs nolli ($GH_TOKEN, $GITHUB_TOKEN or gh): nolli view's."""

    def token(self, owner: str, repo: str) -> str | None:
        return github.find_token()

    def describe(self) -> str:
        return "your GitHub token, if you have one"


def token_from_env(env: dict | None = None) -> Token:
    """``NOLLI_GITHUB_TOKEN`` if it is set, else none."""
    value = (os.environ if env is None else env).get("NOLLI_GITHUB_TOKEN")
    return Fixed(value) if value else Token()


def describe(t: build.Target) -> str:
    """Describe a pinned target for its pages: the repository, the pull request or commits, and
    what the change calls itself."""
    if t.kind == "pinned":
        repo, what = t.repo, f"{t.base[:7]}...{t.head[:7]}"
    else:
        k = t.key
        repo, what = k.repo, f"{k.base[:7]}..{k.head[:7]}" if isinstance(k, ChangeKey) else f"@{k.sha[:7]}"
    if t.about.get("pr"):
        what = f"#{t.about['pr']}"
    title = t.about.get("title")
    return f"{repo.format_name() if repo.format_remote() is None else repo.url.removeprefix('github.com/')} {what}" + (f": {title}" if title else "")


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
    target: build.Target  # pinned
    board: Board = field(default_factory=Board)
    state: str = "waiting"  # waiting, building, done, error
    error: str = ""
    key: ModelKey | None = None  # what it found or built
    t0: float = field(default_factory=time.time)
    ended: float = 0.0

    @property
    def name(self) -> str:
        return self.target.format()

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
    """Run a build step in its own process: call ``fn(*args, progress=…)`` and send its result, or
    why it failed, back over ``conn``. Temporary files go under ``tmp``, which the server deletes,
    also when it stops the step."""
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
    """The models a server shows, where their commits come from, and the builds under way.
    ``work``: where build steps keep temporary files; ``here``: targets on this machine are
    shown too (nolli view: a dataset directory of the store, and with ``local``, the repository
    you are in, its branches, ranges and WORKTREE), else GitHub's paths only (nolli serve);
    ``timeout`` and ``max_repo``: None for no limit."""

    def __init__(self, store: ModelStore, sources: Sources, work: Path, *, here: bool = False, local: Path | None = None, budget: int = 34,
                 keep: int | None = None, builds: int = BUILDS, timeout: float | None = TIMEOUT, max_repo: int | None = MAX_REPO,
                 token: Token | None = None):
        self.store, self.sources, self.work, self.here, self.local, self.budget = store, sources, work, here, local, budget
        for old in OLD:
            shutil.rmtree(work / old, ignore_errors=True)
        self.builds, self.timeout, self.max_repo = threading.BoundedSemaphore(builds), timeout, max_repo
        self.processes = processes()
        self.github = token or Token()
        self.token_failed = ""  # why the token could not be had, said once
        self.policy: retention.Policy | None = retention.LeastRecentlyUsed(keep) if keep is not None else None
        self.tidying = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.built: dict[str, ModelKey] = {}  # a build's name -> the model it found or built
        self.lock = threading.Lock()
        self.answers: dict[str, tuple[float, dict]] = {}

    def find_token(self, owner: str, repo: str) -> str | None:
        """Return the GitHub token for ``owner/repo``, or None to ask as nobody. If no token can be had,
        say why once, not on every call."""
        try:
            return self.github.token(owner, repo)
        except (ChangeError, ValueError, ImportError) as exc:
            if str(exc) != self.token_failed:
                self.token_failed = str(exc)
                print(f"nolli: no GitHub token, asking as nobody: {exc}", file=sys.stderr, flush=True)
            return None

    def api(self, owner: str, repo: str, rest: str = "") -> dict:
        """GitHub's answer for ``repos/<owner>/<repo><rest>``, reused for LOOKUP_TTL seconds."""
        return self.get(f"repos/{owner}/{repo}{rest}")

    def get(self, path: str) -> dict:
        """GitHub's answer for ``path`` (``repos/<owner>/<repo>…``), reused for LOOKUP_TTL seconds."""
        hit = self.answers.get(path)
        if hit and time.time() - hit[0] < LOOKUP_TTL:
            return hit[1]
        out = self.ask(path)
        self.answers[path] = (time.time(), out)
        return out

    def ask(self, path: str) -> dict:
        """GitHub's answer for ``path``, with the token for its repository if there is one. A
        token may not see a public repository (an installation token of an app not installed
        there): a 404 with it is asked again as nobody."""
        owner, repo = path.split("/")[1:3]
        tok = self.find_token(owner, repo)
        try:
            return github.api(path, tok)
        except github.NotFound:
            if not tok:
                raise
            return github.api(path, None)

    def plan(self, text: str | None) -> build.Target:
        """Work out what the target ``text`` names now and return it pinned (``build.Target.pin``).
        Raise github.NotFound for a repository the token cannot read, TooLarge, and ChangeError for
        a target this server cannot show or GitHub cannot be asked about."""
        t = build.Target.from_string(text, self.local)
        if t.kind == "github":
            return self.plan_github(t.github)
        if not self.here:
            raise ChangeError("this host shows GitHub's paths: /<owner>/<repo>, …/pull/<n>, …/commit/<sha>")
        if t.kind == "revs" and self.local is None:
            raise ChangeError("this viewer was started outside a git repository: name a GitHub URL or a dataset directory")
        return t.pin()

    def plan_github(self, t: github.Target) -> build.Target:
        info = self.api(t.owner, t.name)
        owner, name = info["full_name"].split("/")  # GitHub's spelling (paths are case-insensitive)
        if self.max_repo is not None and info.get("size", 0) * 1024 > self.max_repo:
            raise TooLarge(f"{owner}/{name} is {info['size'] >> 10} MB on GitHub; this host builds repositories up to {self.max_repo >> 20} MB")
        return build.Target("github", github=dataclasses.replace(t, owner=owner, name=name)).pin(self.get)

    def find_built(self, target: build.Target) -> tuple[ModelKey, Scene] | None:
        """Return the key and scene that the pinned ``target`` names if it is built, else None. Raise
        NotAllowed as the store does."""
        key = target.key or self.built.get(target.format())
        if key is None:
            return None
        kept = self.store.load_change(key) if isinstance(key, ChangeKey) else self.store.load_model(key)
        sc = scenes.open(self.store, key) if build.is_fresh(kept) else None
        return (key, sc) if sc is not None else None

    def start(self, target: build.Target) -> Job:
        """Return the build of the pinned ``target``, reusing the existing one unless it failed, and
        otherwise starting a new one in a thread. The build runs in the context of the request that
        started it."""
        with self.lock:
            job = self.jobs.get(target.format())
            if job and job.state != "error":
                return job
            job = self.jobs[target.format()] = Job(target)
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(self.run_job, job), daemon=True).start()
        return job

    def run_job(self, job: Job) -> None:
        target, progress = job.target, Progress(job.board)  # type: ignore[arg-type]
        with self.builds:
            job.state, job.t0 = "building", time.time()
            try:
                job.key = build.resolve(target, self.sources, progress)  # known before it is built: tidy() leaves it
                build.build(self.store, dataclasses.replace(target, kind="key", key=job.key), self.sources,
                            about={"budget": self.budget}, run=self.run_step, progress=progress)
                self.built[job.name] = job.key
                job.state = "done"
            except Exception as exc:  # noqa: BLE001  # shown on the page; the server goes on
                job.state, job.error = "error", str(exc) if isinstance(exc, (GitError, FetchError)) else f"{type(exc).__name__}: {exc}"
            finally:
                job.ended = time.time()
        self.tidy()

    def run_step(self, fn: Callable[..., Any], *args: Any, progress: Progress) -> Any:
        """Run a build's step ``fn(*args, progress=…)`` in a process of its own, its progress on
        ``progress``; its result. A step running past the timeout is stopped (ChangeError)."""
        (self.work / ".tmp").mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="step-", dir=self.work / ".tmp"))
        recv, send = self.processes.Pipe(duplex=False)
        proc = self.processes.Process(target=child, args=(fn, args, progress.prefix, tmp, send), daemon=True)
        proc.start()
        send.close()
        deadline, ended = (time.time() + self.timeout if self.timeout else None), None
        try:
            while ended is None:
                left = deadline - time.time() if deadline else 1.0
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
        """Keep the store and clones under ``work`` within their size (--keep): delete what was
        used longest ago, except clones in use and the models of builds under way. Runs after
        each build, the only thing that adds to them."""
        if not self.policy or not self.tidying.acquire(blocking=False):
            return []
        try:
            chosen = self.policy(retention.scan(self.work, KINDS))
            with self.lock:  # no build starts while what it uses may be going
                active = [j for j in self.jobs.values() if j.state in ("waiting", "building")]
                busy = {self.store.locate(k) for j in active if (k := j.target.key or j.key)} if isinstance(self.store, DirectoryStore) else set()
                held = self.sources.clones if isinstance(self.sources, LocalFirst) else self.sources
                if isinstance(held, CloneCache):
                    busy |= held.list_held()
                deleted = [e for e in chosen if e.path not in busy and retention.delete(self.work, KINDS, e)]
            for e in deleted:
                print(f"nolli serve: deleted {e.kind} {e.path.relative_to(self.work)} ({e.size >> 20} MB, used {retention.ago(e.used)} ago)",
                      file=sys.stderr, flush=True)
            return deleted
        finally:
            self.tidying.release()


def page(title: str, body: str, *, status: str = "", job: str = "") -> bytes:
    """hosted.html with a title and body (HTML), and the build it follows, if any."""
    t = (HERE / "hosted.html").read_text()
    return (t.replace("{{title}}", html.escape(title)).replace("{{body}}", body)
            .replace("{{job}}", html.escape(job, quote=True)).replace("{{status}}", status)).encode()


def viewer_page(key: ModelKey, title: str) -> bytes:
    """Make the viewer's page for one model, carrying the model's key for the page's API calls to
    name. A <base> tag makes its relative links work under any path."""
    t = (HERE / "index.html").read_text()
    head = f'<head>\n<base href="/">\n<meta name="nolli-key" content="{html.escape(key.format_key(), quote=True)}">'
    return t.replace("<head>", head, 1).replace("<title>nolli</title>", f"<title>{html.escape(title)} · nolli</title>", 1).encode()


def parse_spec(q: dict[str, str]) -> ProjectionSpec:
    """Parse the viewer's URL parameters (the same keys as its location hash) into a scene state."""
    st = ProjectionSpec()
    if q.get("x"):
        st.expand = projection.Ids(tuple(x for x in q["x"].split(",") if x))
    elif q.get("p") in changes.PRESETS:
        st.expand = changes.PRESETS[q["p"]]()
    if q.get("b") and isinstance(st.expand, projection.AtMost):  # the default cut's budget
        st.expand = projection.AtMost(int(q["b"]))
    if q.get("r") is not None:
        st.roles = [r for r in q["r"].split(",") if r]
    if q.get("g") is not None:
        st.groups = [x for x in q["g"].split(",") if x]
    st.utilities = 0 if q.get("nu") == "1" else int(q["ut"]) if q.get("ut") else DEFAULT_UTILITY_IN
    if q.get("s") in ("base", "head"):
        st.side = q["s"]
    if q.get("ml"):
        st.max_lines = max(1, int(q["ml"]))
    return st


@dataclass
class Services:
    """What a server works with. A service built on it passes its own (a subclass, for more)."""

    store: ModelStore
    reviews: Reviews
    site: Site
    uploads: bool = False  # POST /api/verdicts stores a review's verdicts (nolli view)

    @property
    def sources(self) -> Sources:
        return self.site.sources



class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler, services: Services):
        super().__init__(address, handler)
        self.services = services


class Handler(SimpleHTTPRequestHandler):
    server: Server

    @property
    def services(self) -> Services:
        return self.server.services

    @property
    def site(self) -> Site:
        return self.services.site

    def query(self) -> dict:
        return dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))

    # -- pages ---------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?")[0])
        if path == "/":
            q = self.query()
            if "t" in q:
                self.show(q["t"] or None, q["t"])
            else:
                self.send_page(page("nolli", LANDING))
            return
        if path == "/api/job":
            name = self.query().get("j", "")
            job = self.site.jobs.get(name)
            self.send_json(job.status() if job else {"state": "done" if name in self.site.built else "unknown"})
            return
        if path.startswith("/api/"):
            self.answer_api(path)
            return
        if self.static(path):
            super().do_GET()
            return
        if path.count("/") >= 2 and not path.startswith("//"):  # /<owner>/<repo>…: GitHub's path
            self.show("https://github.com" + path.rstrip("/"), path.strip("/"))
            return
        self.send_page(page("Not a nolli page", NOT_A_PAGE), 404)

    def static(self, path: str) -> bool:
        """Tell whether ``path`` is a file of the viewer itself (its scripts and styles) rather than a
        repository's path."""
        f = (HERE / path.lstrip("/")).resolve()
        return f.is_file() and f.is_relative_to(HERE) and f.suffix in (".js", ".css", ".html", ".svg", ".png")

    def show(self, text: str | None, what: str) -> None:
        """Send the page for the target ``text``: the viewer if it is built, else a page that follows
        its build, or an error page saying why it cannot be shown."""
        github_path = text is not None and text.startswith("https://github.com/")
        try:
            target = self.site.plan(text)
            found = self.site.find_built(target)
        except (github.NotFound, NotAllowed):
            self.send_page(page("Not found, or private", PRIVATE.format(what=html.escape(what))), 404)
            return
        except TooLarge as exc:
            self.send_page(page("Too large for this host", f"<h1>Too large for this host</h1><p>{html.escape(str(exc))}.</p>"), 413)
            return
        except (ChangeError, GitError, KeyError) as exc:
            if github_path and "nolli builds" not in str(exc):
                self.send_page(page("GitHub did not answer", f"<p>{html.escape(str(exc))}</p>"), 502)
            else:
                self.send_page(page("Nothing to show", NOTHING.format(what=html.escape(what), why=html.escape(str(exc)))), 404)
            return
        if found is not None:
            self.send_page(viewer_page(found[0], describe(target)))
            return
        job = self.site.start(target)
        self.send_page(page(describe(target), BUILDING.format(title=html.escape(describe(target))), job=job.name,
                            status=html.escape(json.dumps(job.status()), quote=True)))

    def send_page(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- the API -------------------------------------------------------------------

    def open_model(self, text: str | None) -> tuple[ModelKey, Scene] | None:
        """Open the scene of the model whose key is ``text`` through the store and return it with
        the key; return None if there is no such model or the store refuses it."""
        key = ModelKey.parse(text or "")
        if not isinstance(key, (CommitKey, ChangeKey)):
            return None
        try:
            sc = scenes.open(self.services.store, key)
        except NotAllowed:
            return None
        return (key, sc) if sc is not None else None

    def find_sources(self, key: ModelKey) -> Sources | None:
        """Where a review agent run on ``key`` gets the change's commits: None if this server
        cannot have them (a local repository's, on a server without it)."""
        local = self.site.local
        return self.services.sources if key.repo.format_remote() or (local and key.repo == Repo(str(local.resolve()))) else None

    def review_status(self, key: ModelKey, m: Scene) -> dict:
        """Collect what the review panel shows: where the verdicts came from, the run under way or the
        last one, and whether a run can be started here, and if not, why not."""
        reviews = self.services.reviews
        why = reviews.reviewer.available()
        if why is None and not isinstance(key, ChangeKey):
            why = "dataset"
        if why is None and self.find_sources(key) is None:
            why = "repo"
        job = reviews.job(key)
        return {"verdicts": verdicts.find_provenance(self.services.store, key), "run": job.status() if job else None,
                "can_run": why is None, "why_not": why, "uploads": self.services.uploads,
                "model": reviews.reviewer.model(), "server": "view" if self.site.here else "serve"}

    def answer_api(self, path: str) -> None:
        q = self.query()
        t0 = time.perf_counter()
        found = self.open_model(q.get("k"))
        if found is None:
            self.send_json({"error": f"no model {q.get('k')!r}"}, 404)
            return
        key, sc = found
        t1 = time.perf_counter()
        if path == "/api/scene":
            try:
                out = viewer.export_scene(transformations.apply(sc, projection.fold(parse_spec(q))))
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            meta = sc.model.meta or {}
            out["dataset"] = {"key": q["k"], "repo": meta.get("repo"), "commit": meta.get("commit"), "files": meta.get("files"),
                              "entities": len(sc.model.entities), "relations": len(sc.model.edges)}
            out["stats"]["ms"] = {"load": round((t1 - t0) * 1000), "scene": round((time.perf_counter() - t1) * 1000)}
            self.send_json(out)
        elif path == "/api/mermaid":
            try:
                drawn = transformations.apply(sc, projection.fold(parse_spec(q)))
                text, ids = mermaid.render(drawn, fade=q.get("nodim") != "1"), mermaid.list_ids(drawn)
            except (ValueError, KeyError) as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            if q.get("format") == "json":
                self.send_json({"mermaid": text, "ids": ids})
            else:
                self.send_text(text)
        elif path == "/api/changes":
            is_change = merge.is_change(sc)
            self.send_json({"items": changes.list_changes(sc) if is_change else [],
                            "verdicts": verdicts.describe_verdicts(sc, verdicts.load_verdicts(self.services.store, key)) if is_change else []})
        elif path == "/api/brief":
            if not isinstance(key, ChangeKey):
                self.send_json({"error": "not a change"}, 404)
                return
            self.send_text(brief.make_brief(sc), "text/markdown; charset=utf-8")
        elif path == "/api/review":
            self.send_json(self.review_status(key, sc))
        else:
            self.send_json({"error": f"no {path}"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """POST /api/verdicts?k=<key>: store a review's verdicts (validated); /api/review?k=:
        start a review agent; /api/review/cancel?k=: stop it."""
        path = unquote(self.path.split("?")[0])
        found = self.open_model(self.query().get("k"))
        if path in ("/api/review", "/api/review/cancel") and found is not None:
            key, m = found
            st = self.review_status(key, m)
            if path == "/api/review/cancel":
                self.services.reviews.cancel(key)
            elif not st["can_run"]:
                self.send_json({"error": f"no review agent here ({st['why_not']})", **st}, 409)
                return
            else:
                self.services.reviews.start(self.services.store, key, self.find_sources(key))  # type: ignore[arg-type]
            self.send_json(self.review_status(key, m))
            return
        uploads = self.services.uploads
        if path != "/api/verdicts" or found is None or not uploads:
            self.send_json({"error": "POST /api/verdicts?k=<key> only" if uploads else "verdicts are not stored here"},
                           404 if uploads else 405)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        if not isinstance(found[0], ChangeKey):
            self.send_json({"error": "not a change"}, 400)
            return
        try:
            v = verdicts.read_verdicts(body, verdicts.find_diagram_ids(found[1]))
        except (ValueError, KeyError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        verdicts.save_verdicts(self.services.store, found[0], v, {"by": "upload"})
        self.send_json({"saved": len(v)})

    def send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


LANDING = """<h1>nolli</h1>
<p>A pull request drawn as a change to the repository's architecture. Take a link to a public
GitHub repository, pull request or commit, and replace <code>github.com</code> with this host.</p>
<form id="go"><input id="url" placeholder="https://github.com/owner/repo/pull/123" autofocus><button>Show</button></form>
<script>
document.getElementById("go").addEventListener("submit", e => {
  e.preventDefault();
  const v = document.getElementById("url").value.trim();
  const gh = v.replace(/^(https?:\\/\\/)?(www\\.)?github\\.com/, "");
  location.href = gh !== v || /^[^/#.][^/]*\\/[^/]+/.test(v) ? "/" + gh.replace(/^\\/+/, "") : "/?t=" + encodeURIComponent(v);
});
</script>"""

BUILDING = """<h1>{title}</h1>
<p>Not drawn yet: reading the repository and building its model. This page shows it when it is ready.</p>
<pre id="log"></pre>"""

PRIVATE = """<h1>Not found, or private</h1>
<p>This host cannot read <code>{what}</code> on GitHub: it does not exist, or it is private
and this host may not show it to you.</p>"""

NOTHING = """<h1>Nothing to show</h1>
<p><code>{what}</code>: {why}.</p>"""

NOT_A_PAGE = """<h1>Not a nolli page</h1>
<p>Paths here are GitHub's: <code>/&lt;owner&gt;/&lt;repo&gt;</code>, <code>…/pull/&lt;n&gt;</code>,
<code>…/commit/&lt;sha&gt;</code>. <a href="/">Start here</a>.</p>"""


def make_server(store: ModelStore, sources: Sources, work: Path, host: str = "127.0.0.1", port: int = 8730, *,
                here: bool = False, local: Path | None = None, budget: int = 34, keep: int | None = None, builds: int = BUILDS,
                timeout: float | None = TIMEOUT, max_repo: int | None = MAX_REPO, token: Token | None = None,
                uploads: bool = False, handler: type[Handler] | None = None, reviewer=None,
                services: type[Services] = Services, **more: Any) -> Server:
    """Make a server for the models of ``store``, built from ``sources``, but do not start serving.
    Temporary files and review runs go under ``work``. Port 0 picks a free port. ``here`` and
    ``local`` mean the same as for Site. ``handler`` is a subclass of Handler that adds a service's
    own routes; ``reviewer`` runs reviews (default: nolli.review.agent.Reviewer); ``services`` is a
    subclass of Services, given ``more`` of its fields."""
    site = Site(store, sources, work, here=here, local=local, budget=budget, keep=keep, builds=builds, timeout=timeout,
                max_repo=max_repo, token=token)
    threading.Thread(target=site.tidy, daemon=True).start()  # a smaller --keep than last time takes effect now
    svc = services(store, Reviews(reviewer, work / ".reviews"), site, uploads=uploads, **more)
    return Server((host, port), partial(handler or Handler, directory=str(HERE)), svc)


def make_local_server(data: Path, local: Path | None, host: str = "127.0.0.1", port: int = 8720, budget: int = 34) -> Server:
    """nolli view's server: the store under ``data``, commits from ``local`` (the repository you
    are in, if any) or clones under ``data/.repos``, no limits, your GitHub token, and
    uploaded verdicts stored."""
    store = DirectoryStore(data)
    clones = CloneCache(store.root / ".repos", lambda owner, name: github.find_token())
    return make_server(store, LocalFirst(local, clones), store.root, host, port, here=True, local=local, budget=budget,
                       timeout=None, max_repo=None, token=Yours(), uploads=True)


def main(argv: list[str] | None = None, token: Token | None = None, handler: type[Handler] | None = None,
         reviewer=None, store: ModelStore | None = None, sources: Sources | None = None,
         services: type[Services] = Services, **more: Any) -> int:
    """``nolli serve``: its options from ``argv``; the rest as make_server() takes them. ``store``
    and ``sources`` default to directories under --cache (``data/``, ``repos/``)."""
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
    # git: no credentials, prompts, or this machine's configuration (an insteadOf could reach private repos by ssh)
    os.environ.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ASKPASS="true",
                      SSH_ASKPASS="true", GIT_SSH_COMMAND="false")
    cache = Path(args.cache).expanduser().resolve()
    token = token if token is not None else token_from_env()
    store = store or DirectoryStore(cache / "data")
    holder: list[Site] = []
    sources = sources or CloneCache(cache / "repos", lambda owner, name: holder[0].find_token(owner, name))
    srv = make_server(store, sources, cache, args.host, args.port, budget=args.budget, keep=args.keep, builds=args.builds,
                      timeout=args.timeout, max_repo=args.max_repo or None, token=token, handler=handler, reviewer=reviewer,
                      services=services, **more)
    holder.append(srv.services.site)
    print(f"nolli serve: http://{args.host}:{srv.server_address[1]}/  (cache {cache}; {token.describe()})",
          file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
