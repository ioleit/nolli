"""The viewer's server, for ``nolli serve`` and ``nolli view``.

    nolli serve [--cache DIR] [--keep SIZE] [--builds N] [--timeout S] [--max-repo SIZE] [--host H] [--port N]
    nolli view [<target>] …     (a server on the data directory and the repository you are in)

A page shows one model, named by a target (``nolli --help`` lists the forms):

- GitHub's paths with this host: ``/<owner>/<repo>``, ``…/tree/<ref>``, ``…/commit/<sha>``,
  ``…/compare/<a>...<b>``, ``…/pull/<n>``;
- ``/?t=<target>``, any target, on a server started on a local repository (``nolli view``): a
  branch, a range, ``WORKTREE``, ``#<n>``, or a model's key (``nolli view`` names a dataset
  directory by the key it imported it under).

A target is resolved on every visit, so a new push or a new edit is a new model. What it names
is built in the background if the store does not have it yet (a page says how far it got, and
shows the model when it is ready), and kept in its repository's store, keyed by its commits
(nolli/store/).
Each commit's model is kept too, so a pull request's base is extracted once for all its pushes.

The steps of a build that take time run in processes of their own, ``--builds`` at the same
time (the rest wait). ``nolli serve`` stops a step running past ``--timeout`` and does not
build a repository GitHub says is larger than ``--max-repo``: anyone can ask for any public
repository. With ``--keep 20G`` its clones stay within that size: after each build, the clones
used longest ago are deleted (nolli/server/retention.py). What is extracted is the store's to
keep in bounds (``RepoStoreProvider.cleanup``, which keeps everything for now).

The viewer's page asks the API about the model it shows by its key (``k=<key>``,
``ModelKey.format_key()``): ``/api/scene?k=…&f=<format>&…`` the model projected as a scene
state says (``ProjectionParams``, the viewer's location-hash keys;
nolli/logic/transformations/projection/spec.py; ``p=none``: not projected), in a format
(``FORMATS``: ``viewer`` by default, the viewer's JSON with every overlay; ``table``, rows of the
overlays named; ``mermaid``; ``brief``, what a review agent gets; ``report``; ``comments``, every
comment on the model, nolli/logic/comments.py) with that format's options
(``FormatSceneRequest``, nolli/logic/formats/options.py). A change's list of changes is a table
of the changes preset (``f=table&required_overlay=change&overlays=change&p=changes``), its
comments ``f=comments&p=none``. ``/api/job?j=`` is a build's progress.

GitHub's API is asked with a token (``Token``): for ``nolli serve`` ``NOLLI_GITHUB_TOKEN`` if
it is set (5,000 requests an hour instead of 60), else as nobody; ``nolli view`` uses yours
($GH_TOKEN, $GITHUB_TOKEN or gh). ``nolli serve`` shows whatever its token can read, to anyone
who can reach it, and runs git with none of this machine's credentials or configuration.
Deciding who may see what is the store provider's job (and the sources'): the server opens a
repository's store once per request (``RepoStoreProvider.open``, which asks ``allow``, the
method a service overrides), a refusal raises NotAllowed, and the page answers 404 as for a missing
repository. The store it gets keeps that repository's models only, so a request cannot read
another's by mistake.

A service built on this one passes make_server() (and main()) parts of its own: a Token (one
per repository, say a GitHub App's installation token for each installation), a store
provider and sources (which may decide per request who may read what), an ``APIRouter`` of
pages and routes of its own (tried before nolli's), middleware around every request (say, to
tell who is asking), and a subclass of Services for what those need, which may also draw
nolli's own pages (``Services.render_page``, a ``Page`` of a kind in ``PAGE_KINDS``). A build runs in the context (contextvars) of the request that started it, so a provider
that checks who asks sees that request when the build opens its repository's store.
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
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from starlette.middleware import Middleware
from pydantic import BaseModel, ConfigDict

import nolli.viewer  # noqa: E402  (the page, its scripts and styles)

HERE = Path(nolli.viewer.__file__).resolve().parent  # the viewer's files

from nolli import operations  # noqa: E402
from nolli.logic.formats import formatters  # noqa: E402
from nolli.logic.formats.options import MermaidFormatOptions, TableFormatOptions  # noqa: E402

from ..logic.sources import CloneCache, FetchError, LocalFirst, Sources, github  # noqa: E402
from ..logic.sources.git import ChangeError, GitError  # noqa: E402
from nolli.logic import transformations  # noqa: E402
from nolli.logic.transformations import merge, projection  # noqa: E402
from nolli.logic.transformations.merge import presets  # noqa: E402

from ..logic.transformations.projection import ProjectionSpec  # noqa: E402
from ..model.keys import ChangeKey, CommitKey, ModelKey, NotAllowed, Repo  # noqa: E402
from ..model.scene import Scene  # noqa: E402
from ..progress import Progress  # noqa: E402
from nolli.store import ModelStore, RepoStoreProvider  # noqa: E402
from . import retention  # noqa: E402

LOOKUP_TTL = 60  # s: how long an answer from GitHub's API is reused
BUILDS = min(4, os.cpu_count() or 1)  # builds at the same time; the rest wait their turn
TIMEOUT = 600  # s: a step of a build taking longer is stopped (nolli serve)
MAX_REPO = 1 << 30  # bytes: GitHub's size of a repository (all its history) above which it is not built (nolli serve)
KINDS = {"clone": "repos/*/*"}  # what retention may delete: nolli serve's clones
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


def describe(t: operations.Target) -> str:
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
    target: operations.Target  # pinned
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
        ctx.set_forkserver_preload(["nolli.operations"])
        return ctx
    return multiprocessing.get_context("spawn")


class Site:
    """The models a server shows, where their commits come from, and the builds under way.
    ``work``: where build steps keep temporary files; ``here``: targets on this machine are
    shown too (nolli view: with ``local``, the repository
    you are in, its branches, ranges and WORKTREE), else GitHub's paths only (nolli serve);
    ``timeout`` and ``max_repo``: None for no limit."""

    def __init__(self, stores: RepoStoreProvider, sources: Sources, work: Path, *, here: bool = False, local: Path | None = None, budget: int = 34,
                 keep: int | None = None, builds: int = BUILDS, timeout: float | None = TIMEOUT, max_repo: int | None = MAX_REPO,
                 token: Token | None = None):
        self.stores, self.sources, self.work, self.here, self.local, self.budget = stores, sources, work, here, local, budget
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

    def plan(self, text: str | None) -> operations.Target:
        """Work out what the target ``text`` names now and return it pinned (``operations.Target.pin``).
        Raise github.NotFound for a repository the token cannot read, TooLarge, and ChangeError for
        a target this server cannot show or GitHub cannot be asked about."""
        t = operations.Target.from_string(text, self.local)
        if t.kind == "github":
            return self.plan_github(t.github)
        if not self.here:
            raise ChangeError("this host shows GitHub's paths: /<owner>/<repo>, …/pull/<n>, …/commit/<sha>")
        if t.kind == "revs" and self.local is None:
            raise ChangeError("this viewer was started outside a git repository: name a GitHub URL or a dataset directory")
        return t.pin()

    def plan_github(self, t: github.Target) -> operations.Target:
        info = self.api(t.owner, t.name)
        owner, name = info["full_name"].split("/")  # GitHub's spelling (paths are case-insensitive)
        if self.max_repo is not None and info.get("size", 0) * 1024 > self.max_repo:
            raise TooLarge(f"{owner}/{name} is {info['size'] >> 10} MB on GitHub; this host builds repositories up to {self.max_repo >> 20} MB")
        return operations.Target("github", github=dataclasses.replace(t, owner=owner, name=name)).pin(self.get)

    def find_built(self, target: operations.Target) -> tuple[ModelKey, Scene] | None:
        """Return the key and scene that the pinned ``target`` names if it is built, else None. Raise
        NotAllowed if this request may not read its repository, also when nothing is built yet: a
        page asks before it starts a build."""
        store = self.stores.open(target.key.repo if target.key else target.repo)
        key = target.key or self.built.get(target.format())
        if key is None:
            return None
        kept = store.load_change(key) if isinstance(key, ChangeKey) else store.load_model(key)
        sc = operations.open_scene(store, key) if operations.is_fresh(kept) else None
        return (key, sc) if sc is not None else None

    def start(self, target: operations.Target) -> Job:
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
                job.key = operations.resolve_target(target, self.sources, progress)  # known before it is built: tidy() leaves it
                store = self.stores.open(job.key.repo)  # in the context of the request that started the build
                operations.build_target(store, dataclasses.replace(target, kind="key", key=job.key), self.sources,
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
        """Keep the clones under ``work`` within their size (--keep): delete those used longest
        ago, except clones in use. Then let the store keep itself in bounds (``cleanup``). Runs
        after each build, the only thing that adds to them."""
        if not self.tidying.acquire(blocking=False):
            return []
        try:
            self.stores.cleanup()
            if not self.policy:
                return []
            chosen = self.policy(retention.scan(self.work, KINDS))
            with self.lock:  # no build starts while what it uses may be going
                held = self.sources.clones if isinstance(self.sources, LocalFirst) else self.sources
                busy = held.list_held() if isinstance(held, CloneCache) else set()
                deleted = [e for e in chosen if e.path not in busy and retention.delete(self.work, KINDS, e)]
            for e in deleted:
                print(f"nolli serve: deleted {e.kind} {e.path.relative_to(self.work)} ({e.size >> 20} MB, used {retention.ago(e.used)} ago)",
                      file=sys.stderr, flush=True)
            return deleted
        finally:
            self.tidying.release()


@dataclass
class Page:
    """A page the server answers with, other than the viewer: what it says (``kind``, one of
    ``PAGE_KINDS``), its title and body (HTML), its status code, ``detail`` (why it cannot show
    what was asked for, as text) and, for a build's page, the build it follows (``job``, its
    first ``status``). ``Services.render_page`` draws it."""

    kind: str
    title: str
    body: str
    code: int = 200
    detail: str = ""
    job: str = ""
    status: dict | None = None


PAGE_KINDS = ("landing", "building", "not_found", "too_large", "github_error", "nothing", "not_a_page")


def render_page(page: Page) -> bytes:
    """Fill hosted.html with ``page``: its title and body, and the build it follows, if any."""
    t = (HERE / "hosted.html").read_text()
    status = html.escape(json.dumps(page.status), quote=True) if page.status is not None else ""
    return (t.replace("{{title}}", html.escape(page.title)).replace("{{body}}", page.body)
            .replace("{{job}}", html.escape(page.job, quote=True)).replace("{{status}}", status)).encode()


def viewer_page(key: ModelKey, title: str) -> bytes:
    """Make the viewer's page for one model, carrying the model's key for the page's API calls to
    name. A <base> tag makes its relative links work under any path."""
    t = (HERE / "index.html").read_text()
    head = f'<head>\n<base href="/">\n<meta name="nolli-key" content="{html.escape(key.format_key(), quote=True)}">'
    return t.replace("<head>", head, 1).replace("<title>nolli</title>", f"<title>{html.escape(title)} · nolli</title>", 1).encode()


def parse_spec(q: dict[str, str]) -> ProjectionSpec:
    """Parse the viewer's URL parameters (the same keys as its location hash) into a scene state,
    with every preset the viewer offers (``presets.PRESETS``)."""
    return ProjectionSpec.from_query(q, presets.PRESETS)


# ---------- the scene request ----------

FORMATS: dict[str, formatters.Formatter] = dict(formatters.FORMATS)
FormatName = Literal[tuple(FORMATS)]  # type: ignore[valid-type]


class ProjectionParams(BaseModel):
    """The projection a scene request asks for, under the viewer's location-hash keys
    (``ProjectionSpec.from_query`` reads them). None of them gives the default cut; ``p=none``
    no projection at all."""

    x: str | None = None  # the unfolded containers, comma-separated
    p: str | None = None  # a preset (presets.PRESETS, the named expansions), or "none"
    d: str | None = None  # p=dirs: the directory under which
    b: int | None = None  # the default cut's budget
    r: str | None = None  # the roles shown, comma-separated ("" for none)
    g: str | None = None  # the groups shown, comma-separated
    nu: bool = False  # no utilities
    ut: int | None = None  # utilities: used by at least this many
    s: Literal["base", "head"] | None = None  # one side of a change
    ml: int | None = None  # the most lines of a box

    def to_spec(self) -> ProjectionSpec | None:
        """Return the projection asked for, or None for none (``p=none``)."""
        if self.p == "none":
            return None
        q = {}
        for name in ProjectionParams.model_fields:
            v = getattr(self, name)
            if v is not None and v is not False:
                q[name] = "1" if v is True else str(v)
        return parse_spec(q)


class FormatSceneRequest(ProjectionParams, MermaidFormatOptions, TableFormatOptions):
    """A scene request: the model ``k``, projected, in the format ``f``. A formatter with options of
    its own adds its options model to the bases (no field may be in two of them); options of a
    formatter other than ``f`` are ignored, any other parameter is an error."""

    model_config = ConfigDict(extra="forbid")
    k: str | None = None  # the model's key (ModelKey.format_key)
    f: FormatName = "viewer"  # type: ignore[valid-type]


@dataclass
class Services:
    """What a server works with, and what its routes ask of it. A service built on it passes its
    own (a subclass, for more)."""

    stores: RepoStoreProvider
    site: Site

    @property
    def sources(self) -> Sources:
        return self.site.sources

    def open_model(self, text: str | None) -> tuple[ModelKey, Scene, ModelStore] | None:
        """Open the scene of the model whose key is ``text`` and return it with the key and its
        repository's store, opened once for this request; return None if there is no such model or
        this request may not read its repository."""
        try:
            key = ModelKey.parse(text or "")
        except ValueError:
            return None
        if not isinstance(key, (CommitKey, ChangeKey)):
            return None
        try:
            store = self.stores.open(key.repo)
            sc = operations.open_scene(store, key)
        except NotAllowed:
            return None
        return (key, sc, store) if sc is not None else None

    def render_page(self, request: Request, page: Page) -> Response:
        """Answer ``page`` to ``request``. A service overrides this to draw nolli's pages its own way."""
        return html_response(render_page(page), page.code)

    def may_read(self, repo: Repo) -> bool:
        """Say whether the current request may read ``repo`` (``RepoStoreProvider.allow``)."""
        try:
            self.stores.allow(repo)
        except NotAllowed:
            return False
        return True


# ---------- responses ----------

def html_response(body: bytes, status: int = 200) -> Response:
    return Response(body, status, media_type="text/html; charset=utf-8")


def json_response(obj: Any, status: int = 200) -> Response:
    """Answer ``obj`` as JSON, written as ``json.dumps`` writes it (NaN included, unlike FastAPI's own)."""
    return Response(json.dumps(obj).encode(), status, media_type="application/json")


def text_response(text: str, media_type: str = "text/plain; charset=utf-8") -> Response:
    return Response(text.encode(), media_type=media_type)


def services_of(request: Request) -> Services:
    """Return the Services of the server ``request`` came to; a service's own routes use it too."""
    return request.app.state.services


def query_of(request: Request) -> dict[str, str]:
    """Return the request's URL parameters, the last value of each, blank ones kept."""
    return dict(request.query_params)


def no_model(q: dict[str, str]) -> Response:
    return json_response({"error": f"no model {q.get('k')!r}"}, 404)


def static_file(path: str) -> Path | None:
    """Return the viewer's own file (a script, a style, a page) that ``path`` names, or None for any
    other path, a repository's say."""
    f = (HERE / path.lstrip("/")).resolve()
    return f if f.is_file() and f.is_relative_to(HERE) and f.suffix in (".js", ".css", ".html", ".svg", ".png") else None


def show(request: Request, text: str | None, what: str) -> Response:
    """Answer the page for the target ``text``: the viewer if it is built, else a page that follows
    its build, or an error page saying why it cannot be shown."""
    services = services_of(request)
    site, github_path = services.site, text is not None and text.startswith("https://github.com/")
    try:
        target = site.plan(text)
        found = site.find_built(target)
    except (github.NotFound, NotAllowed):
        return services.render_page(request, Page("not_found", "Not found, or private", PRIVATE.format(what=html.escape(what)), 404))
    except TooLarge as exc:
        return services.render_page(request, Page("too_large", "Too large for this host",
                                                  f"<h1>Too large for this host</h1><p>{html.escape(str(exc))}.</p>", 413, str(exc)))
    except (ChangeError, GitError, KeyError) as exc:
        if github_path and "nolli builds" not in str(exc):
            return services.render_page(request, Page("github_error", "GitHub did not answer", f"<p>{html.escape(str(exc))}</p>", 502, str(exc)))
        return services.render_page(request, Page("nothing", "Nothing to show",
                                                  NOTHING.format(what=html.escape(what), why=html.escape(str(exc))), 404, str(exc)))
    if found is not None:
        return html_response(viewer_page(found[0], describe(target)))
    job = site.start(target)
    return services.render_page(request, Page("building", describe(target), BUILDING.format(title=html.escape(describe(target))),
                                              job=job.name, status=job.status()))


# ---------- the routes ----------
# Plain ``def`` routes: FastAPI runs them in its thread pool, each in a copy of the request's
# context (contextvars), so a store provider that checks who asks sees this request.

router = APIRouter()


@router.get("/")
def landing(request: Request) -> Response:
    """Answer the landing page, or with ``?t=<target>`` that target's page (nolli view)."""
    q = query_of(request)
    if "t" in q:
        return show(request, q["t"] or None, q["t"])
    return services_of(request).render_page(request, Page("landing", "nolli", LANDING))


@router.get("/api/job")
def job_status(request: Request) -> Response:
    """Answer a build's progress: ``?j=<build>``, the name a building page follows. A build of a
    repository the request may not read is unknown, as one that never was."""
    services, name = services_of(request), query_of(request).get("j", "")
    site = services.site
    job, built = site.jobs.get(name), site.built.get(name)
    repo = (job.key or job.target.key or job.target).repo if job else built.repo if built else None
    if repo is None or not services.may_read(repo):
        return json_response({"state": "unknown"})
    return json_response(job.status() if job else {"state": "done"})


@router.get("/api/scene")
def scene(request: Request, q: Annotated[FormatSceneRequest, Query()]) -> Response:
    """Answer the model ``k``, projected as asked, in the format ``f``, which is the viewer's JSON
    unless it says otherwise. ``p=none`` asks for no projection. The Server-Timing header says
    how long loading, projecting and formatting took."""
    t0 = time.perf_counter()
    found = services_of(request).open_model(q.k)
    if found is None:
        return json_response({"error": f"no model {q.k!r}"}, 404)
    sc = found[1]
    t1 = time.perf_counter()
    try:
        spec = q.to_spec()
        drawn = sc if spec is None else transformations.apply(sc, projection.fold(spec))
        t2 = time.perf_counter()
        out = FORMATS[q.f](drawn, q)
    except (ValueError, KeyError) as exc:
        return json_response({"error": f"{type(exc).__name__}: {exc}"}, 400)
    t3 = time.perf_counter()
    response = json_response(out)
    response.headers["Server-Timing"] = ", ".join(f"{name};dur={(b - a) * 1000:.0f}" for name, a, b in
                                                  (("load", t0, t1), ("project", t1, t2), ("format", t2, t3)))
    return response


@router.get("/{path:path}")
def any_path(path: str, request: Request) -> Response:
    """Answer the viewer's own files, and GitHub's paths (``/<owner>/<repo>…``) as pages."""
    if path.startswith("api/"):
        return json_response({"error": f"no /{path}"}, 404)
    f = static_file(path)
    if f is not None:
        return FileResponse(f)
    if path.count("/") >= 1 and not path.startswith("/"):  # <owner>/<repo>…: GitHub's path
        return show(request, "https://github.com/" + path.rstrip("/"), path.strip("/"))
    return services_of(request).render_page(request, Page("not_a_page", "Not a nolli page", NOT_A_PAGE, 404))


def make_app(services: Services, routes: APIRouter | None = None, middleware: Sequence[Middleware] = ()) -> FastAPI:
    """Make the server's app on ``services``: a service's own ``routes`` first (they may stand in for
    nolli's), then nolli's; a service's ``middleware`` around every route, its own and nolli's. No
    response is cached by the browser."""
    app = FastAPI(title="nolli", docs_url=None, redoc_url=None, openapi_url=None, middleware=list(middleware))
    app.state.services = services

    @app.exception_handler(RequestValidationError)
    async def bad_request(_request: Request, exc: RequestValidationError) -> Response:
        """Answer a request whose parameters are wrong with 422. Say which are wrong and why in
        ``error``, as every other error does, and give FastAPI's own list in ``detail``."""
        errors = jsonable_encoder(exc.errors())
        said = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', ())[1:]) or 'request'}: {e.get('msg')}" for e in errors)
        return json_response({"error": said, "detail": errors}, 422)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    if routes is not None:
        app.include_router(routes)
    app.include_router(router)
    return app


class Server:
    """A server on a port it holds from the start (port 0: a free one), not serving yet:
    ``serve_forever`` serves until ``shutdown`` (from another thread) or Ctrl-C, ``server_close``
    gives the port back."""

    def __init__(self, app: FastAPI, host: str, port: int):
        self.app = app
        self.socket = socket.create_server((host, port))  # OSError if the port is taken
        self.server_address = self.socket.getsockname()[:2]
        self.uvicorn = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off",
                                                     timeout_graceful_shutdown=1))
        self.running = threading.Event()
        self.stopped = threading.Event()

    @property
    def services(self) -> Services:
        return self.app.state.services

    def serve_forever(self) -> None:
        self.running.set()
        try:
            self.uvicorn.run(sockets=[self.socket])
        finally:
            self.stopped.set()

    def shutdown(self) -> None:
        """Stop serving and wait until it has stopped."""
        self.uvicorn.should_exit = True
        if self.running.is_set():
            self.stopped.wait(10)

    def server_close(self) -> None:
        self.socket.close()



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


def make_server(stores: RepoStoreProvider, sources: Sources, work: Path, host: str = "127.0.0.1", port: int = 8730, *,
                here: bool = False, local: Path | None = None, budget: int = 34, keep: int | None = None, builds: int = BUILDS,
                timeout: float | None = TIMEOUT, max_repo: int | None = MAX_REPO, token: Token | None = None,
                routes: APIRouter | None = None, middleware: Sequence[Middleware] = (), services: type[Services] = Services,
                **more: Any) -> Server:
    """Make a server for the models of the stores ``stores`` opens, built from ``sources``, but do not start serving.
    Temporary files go under ``work``. Port 0 picks a free port. ``here`` and ``local`` mean the
    same as for Site. ``routes`` are a service's own routes (a FastAPI ``APIRouter``;
    ``services_of(request)`` is the server's Services), tried before nolli's; ``middleware``
    (Starlette's ``Middleware``) runs around every request, say to tell who is asking before
    ``RepoStoreProvider.allow`` is asked; ``services`` is a subclass of Services, given ``more`` of
    its fields."""
    site = Site(stores, sources, work, here=here, local=local, budget=budget, keep=keep, builds=builds, timeout=timeout,
                max_repo=max_repo, token=token)
    threading.Thread(target=site.tidy, daemon=True).start()  # a smaller --keep than last time takes effect now
    svc = services(stores, site, **more)
    return Server(make_app(svc, routes, middleware), host, port)


def make_local_server(data: Path, local: Path | None, host: str = "127.0.0.1", port: int = 8720, budget: int = 34) -> Server:
    """nolli view's server: the store under ``data``, commits from ``local`` (the repository you
    are in, if any) or clones under ``data/.repos``, no limits and your GitHub token."""
    stores = RepoStoreProvider(data)
    clones = CloneCache(stores.root / ".repos", lambda owner, name: github.find_token())
    return make_server(stores, LocalFirst(local, clones), stores.root, host, port, here=True, local=local, budget=budget,
                       timeout=None, max_repo=None, token=Yours())


def main(argv: list[str] | None = None, token: Token | None = None, routes: APIRouter | None = None,
         middleware: Sequence[Middleware] = (), stores: RepoStoreProvider | None = None, sources: Sources | None = None,
         services: type[Services] = Services, **more: Any) -> int:
    """``nolli serve``: its options from ``argv``; the rest as make_server() takes them. ``stores``
    and ``sources`` default to directories under --cache (``data/``, ``repos/``)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=os.environ.get("NOLLI_CACHE") or str(Path.home() / ".cache" / "nolli-serve"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8730))
    ap.add_argument("--budget", type=int, default=34)
    ap.add_argument("--keep", type=retention.parse_size, default=os.environ.get("NOLLI_CACHE_KEEP"),
                    help="the clones' size at most, e.g. 20G: those used longest ago go first (default: keep every one)")
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
    stores = stores or RepoStoreProvider(cache / "data")
    holder: list[Site] = []
    sources = sources or CloneCache(cache / "repos", lambda owner, name: holder[0].find_token(owner, name))
    srv = make_server(stores, sources, cache, args.host, args.port, budget=args.budget, keep=args.keep, builds=args.builds,
                      timeout=args.timeout, max_repo=args.max_repo or None, token=token, routes=routes,
                      middleware=middleware, services=services, **more)
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
