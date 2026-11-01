"""The GitHub token of nolli serve without GitHub: one per repository, from the environment or
from a service built on it, falling back to nobody, and the server refusing a private
repository whatever its token sees."""

from __future__ import annotations

import pytest

from nolli import github
from nolli.viewer import hosted


def test_from_env():
    assert hosted.token_from_env({}).token("o", "r") is None
    assert hosted.token_from_env({"NOLLI_GITHUB_TOKEN": "t"}).token("o", "r") == "t"


def test_each_repository_its_own_token(tmp_path, monkeypatch):
    class PerOwner(hosted.Token):
        def token(self, owner, repo):
            return f"tok-{owner}"

    monkeypatch.setattr(hosted.github, "api", lambda path, tok: {"path": path, "tok": tok})
    site = hosted.Site(tmp_path, token=PerOwner())
    assert site.ask("a", "r") == {"path": "repos/a/r", "tok": "tok-a"}
    assert site.api("b", "r", "/pulls/1") == {"path": "repos/b/r/pulls/1", "tok": "tok-b"}


def test_serve_refuses_private_and_asks_again_as_nobody(tmp_path, monkeypatch):
    asked = []

    def api(path, tok):
        asked.append((path, tok))
        if path == "repos/me/secret":
            return {"full_name": "me/secret", "private": True, "default_branch": "main"}
        if tok:
            raise github.NotFound(path)
        return {"full_name": "o/pub", "private": False}

    monkeypatch.setattr(hosted.github, "api", api)
    site = hosted.Site(tmp_path, token=hosted.Fixed("tok"))
    with pytest.raises(github.NotFound):
        site.plan("me", "secret")
    assert site.ask("o", "pub")["full_name"] == "o/pub"
    assert asked[-2:] == [("repos/o/pub", "tok"), ("repos/o/pub", None)]  # the token does not see it


def test_a_token_that_cannot_be_had_falls_back_to_nobody(tmp_path, monkeypatch, capsys):
    class Broken(hosted.Token):
        def token(self, owner, repo):
            raise github.ChangeError("GitHub App: POST app/installations/1/access_tokens: 401 Unauthorized")

    monkeypatch.setattr(hosted.github, "api", lambda path, tok: {"tok": tok})
    site = hosted.Site(tmp_path, token=Broken())
    assert site.ask("o", "x") == {"tok": None} and site.ask("o", "y") == {"tok": None}
    assert capsys.readouterr().err.count("asking as nobody") == 1


def test_a_service_handler_gets_the_site(tmp_path):
    class Mine(hosted.HostedHandler):
        pass

    srv = hosted.make_server(tmp_path, port=0, token=hosted.Fixed("t"), handler=Mine)
    try:
        assert Mine.site.github.token("o", "r") == "t" and Mine.data_root == Mine.site.data
    finally:
        srv.server_close()
