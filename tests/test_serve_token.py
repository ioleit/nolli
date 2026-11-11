"""The GitHub token of nolli serve (nolli/server/serve.py) without GitHub: one per repository, from the environment or
from a service built on it, falling back to nobody, and the server showing whatever its token
reads."""

from __future__ import annotations

import pytest

from nolli.logic.sources import CloneCache, github
from nolli.server import serve
from nolli.store import DirectoryStore


def make_site(cache, **kw) -> serve.Site:
    return serve.Site(DirectoryStore(cache / "data"), CloneCache(cache / "repos"), cache, **kw)


def test_from_env():
    assert serve.token_from_env({}).token("o", "r") is None
    assert serve.token_from_env({"NOLLI_GITHUB_TOKEN": "t"}).token("o", "r") == "t"


def test_each_repository_its_own_token(tmp_path, monkeypatch):
    class PerOwner(serve.Token):
        def token(self, owner, repo):
            return f"tok-{owner}"

    monkeypatch.setattr(serve.github, "api", lambda path, tok: {"path": path, "tok": tok})
    site = make_site(tmp_path, token=PerOwner())
    assert site.ask("repos/a/r") == {"path": "repos/a/r", "tok": "tok-a"}
    assert site.api("b", "r", "/pulls/1") == {"path": "repos/b/r/pulls/1", "tok": "tok-b"}


def test_serve_shows_what_its_token_reads_and_asks_again_as_nobody(tmp_path, monkeypatch):
    """A private repository the token reads is planned like any other (who may see it is the
    store's decision); a public one the token does not see is asked for again as nobody."""
    asked = []

    def api(path, tok):
        asked.append((path, tok))
        if path.startswith("repos/me/secret"):
            return {"full_name": "me/secret", "private": True, "default_branch": "main", "sha": "a" * 40}
        if tok:
            raise github.NotFound(path)
        return {"full_name": "o/pub", "private": False}

    monkeypatch.setattr(serve.github, "api", api)
    site = make_site(tmp_path, token=serve.Fixed("tok"))
    assert site.plan("https://github.com/me/secret").key == serve.Repo("github.com/me/secret").make_commit("a" * 40)
    assert site.ask("repos/o/pub")["full_name"] == "o/pub"
    assert asked[-2:] == [("repos/o/pub", "tok"), ("repos/o/pub", None)]  # the token does not see it


def test_a_token_that_cannot_be_had_falls_back_to_nobody(tmp_path, monkeypatch, capsys):
    class Broken(serve.Token):
        def token(self, owner, repo):
            raise github.ChangeError("GitHub App: POST app/installations/1/access_tokens: 401 Unauthorized")

    monkeypatch.setattr(serve.github, "api", lambda path, tok: {"tok": tok})
    site = make_site(tmp_path, token=Broken())
    assert site.ask("repos/o/x") == {"tok": None} and site.ask("repos/o/y") == {"tok": None}
    assert capsys.readouterr().err.count("asking as nobody") == 1


def test_a_service_brings_its_handler_parts_and_store(tmp_path):
    """make_server() takes a service's handler, a subclass of Services with its own fields, and
    its own store; nothing is kept on classes, so two servers in one process stay apart."""
    from dataclasses import dataclass

    class Mine(serve.Handler):
        pass

    @dataclass
    class MyServices(serve.Services):
        hooks: str = ""

    store = DirectoryStore(tmp_path / "elsewhere")
    srv = serve.make_server(store, CloneCache(tmp_path / "repos"), tmp_path, port=0, token=serve.Fixed("t"), handler=Mine,
                             services=MyServices, hooks="mine")
    other = serve.make_server(DirectoryStore(tmp_path / "other"), CloneCache(tmp_path / "repos2"), tmp_path / "w", port=0)
    try:
        assert srv.services.site.github.token("o", "r") == "t" and srv.services.store is store and srv.services.hooks == "mine"
        assert srv.RequestHandlerClass.func is Mine  # type: ignore[attr-defined]
        assert other.services.store is not store and other.services.site.github.token("o", "r") is None
    finally:
        srv.server_close()
        other.server_close()
