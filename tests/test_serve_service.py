"""A service built on nolli serve (nolli/server/serve.py) without GitHub: its middleware telling
who asks, its store provider deciding who may read what, and its own look for nolli's pages."""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass

import pytest
from fastapi import Request, Response
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from nolli import operations
from nolli.logic.sources import CloneCache
from nolli.model.keys import NotAllowed, Repo
from nolli.server import serve
from nolli.store import RepoStoreProvider

WHO: ContextVar[str] = ContextVar("who", default="")


class Mine(RepoStoreProvider):
    """Everyone may read o/open; only "alice" may read o/secret."""

    def allow(self, repo: Repo) -> None:
        if repo.url == "github.com/o/secret" and WHO.get() != "alice":
            raise NotAllowed(f"{WHO.get() or 'nobody'} may not read {repo.url}")


async def tell_who(request: Request, call_next):
    token = WHO.set(request.headers.get("X-Who", ""))
    try:
        return await call_next(request)
    finally:
        WHO.reset(token)


@dataclass
class MyServices(serve.Services):
    def render_page(self, request: Request, page: serve.Page) -> Response:
        return serve.json_response({"kind": page.kind, "title": page.title, "code": page.code, "who": WHO.get()}, page.code)


@contextlib.contextmanager
def run_server(tmp_path):
    srv = serve.make_server(Mine(tmp_path / "data"), CloneCache(tmp_path / "repos"), tmp_path, port=0,
                            middleware=[Middleware(BaseHTTPMiddleware, dispatch=tell_who)], services=MyServices)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def get(url: str, who: str = "") -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"X-Who": who} if who else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_a_service_draws_nollis_pages_and_its_middleware_tells_who_asks(tmp_path):
    with run_server(tmp_path) as (_srv, url):
        assert get(f"{url}/", "bob") == (200, {"kind": "landing", "title": "nolli", "code": 200, "who": "bob"})
        assert get(f"{url}/nothing-here")[1]["kind"] == "not_a_page"


def test_a_build_is_unknown_to_whoever_may_not_read_its_repository(tmp_path):
    """/api/job asks the store provider about the build's repository, as every other read does."""
    with run_server(tmp_path) as (srv, url):
        site = srv.services.site
        for name in ("open", "secret"):
            key = Repo(f"github.com/o/{name}").make_commit("a" * 40)
            job = site.jobs[key.format_key()] = serve.Job(operations.Target("key", key=key))
            job.state = "building"
        secret = Repo("github.com/o/secret").make_commit("a" * 40).format_key()
        opened = Repo("github.com/o/open").make_commit("a" * 40).format_key()
        assert get(f"{url}/api/job?j={opened}")[1]["state"] == "building"
        assert get(f"{url}/api/job?j={secret}")[1]["state"] == "unknown"
        assert get(f"{url}/api/job?j={secret}", "alice")[1]["state"] == "building"
        site.jobs.clear()
        site.built[secret] = Repo("github.com/o/secret").make_commit("a" * 40)
        assert get(f"{url}/api/job?j={secret}")[1]["state"] == "unknown"
        assert get(f"{url}/api/job?j={secret}", "alice")[1]["state"] == "done"


def test_a_page_asks_before_it_starts_a_build(tmp_path):
    """A pinned target (a pull request GitHub merges) is not built yet, and its key not known: the
    store provider is asked about its repository all the same, before a build could start."""
    with run_server(tmp_path) as (srv, _url):
        site = srv.services.site
        target = operations.Target("pinned", repo=Repo("github.com/o/secret"), base="a" * 40, head="b" * 40)
        with pytest.raises(NotAllowed):
            site.find_built(target)
        token = WHO.set("alice")
        try:
            assert site.find_built(target) is None
        finally:
            WHO.reset(token)


def test_the_provider_deletes_what_it_kept_of_a_repository(tmp_path):
    stores = Mine(tmp_path / "data")
    kept, other = Repo("github.com/O/Secret"), Repo("github.com/o/open")
    token = WHO.set("alice")
    try:
        for repo in (kept, other):
            stores.open(repo).save_comments(repo.make_commit("a" * 40), [{"id": "c-1"}])
    finally:
        WHO.reset(token)
    stores.delete(Repo("github.com/o/secret"))  # as GitHub spells it in an event, any case; nobody asking
    token = WHO.set("alice")
    try:
        assert stores.open(kept).load_comments(kept.make_commit("a" * 40)) == []
        assert stores.open(other).load_comments(other.make_commit("a" * 40)) == [{"id": "c-1"}]
    finally:
        WHO.reset(token)


def test_github_is_not_asked_about_a_repository_the_request_may_not_read(tmp_path, monkeypatch):
    """Not even its size: a page for it is "not found" before GitHub is asked anything."""
    asked = []
    monkeypatch.setattr(serve.github, "api", lambda path, tok: asked.append(path) or {"full_name": "o/secret", "size": 1 << 30})
    with run_server(tmp_path) as (srv, url):
        assert get(f"{url}/o/secret/pull/1") == (404, {"kind": "not_found", "title": "Not found, or private", "code": 404, "who": ""})
        assert asked == []
        assert get(f"{url}/o/secret/pull/1", "alice")[1]["kind"] == "too_large" and asked == ["repos/o/secret"]
