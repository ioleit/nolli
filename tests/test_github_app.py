"""The GitHub token of nolli serve (nolli/github_app.py) without GitHub: the app's JWT, the
installation token made once and renewed before it expires, the environment that picks one,
and the server refusing a private repository whatever its token sees."""

from __future__ import annotations

import base64
import json
import time

import pytest

from nolli import github, github_app
from nolli.viewer import hosted

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_jwt_is_signed_by_the_key():
    head, body, sig = github_app.jwt("123", PEM, now=1_000_000).split(".")
    assert json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(unb64(body)) == {"iat": 999_940, "exp": 1_000_540, "iss": "123"}
    KEY.public_key().verify(unb64(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())


def installations(n: int) -> list[dict]:
    """The app's installations: n of other people's, then the owner's."""
    return [{"id": i, "account": {"login": f"user{i}"}} for i in range(n)] + [{"id": 42, "account": {"login": "IOleit"}}]


def test_installation_token_made_once_and_renewed(monkeypatch):
    calls = []
    expires = [time.time() + 3600]

    def call(method, path, bearer):
        calls.append((method, path))
        if path == "app":
            return {"owner": {"login": "ioleit"}}
        if path.startswith("app/installations?"):
            page = int(path.rsplit("=", 1)[1])
            return installations(150)[(page - 1) * 100:page * 100]
        return {"token": f"t{len(calls)}", "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires[0]))}

    monkeypatch.setattr(github_app, "call", call)
    t = github_app.Installation("123", PEM)
    assert t.token() == "t4" and t.token() == "t4"  # the owner's installation, on the second page
    assert calls[-1] == ("POST", "app/installations/42/access_tokens") and len(calls) == 4
    t.expires = time.time() + 60  # within RENEW of its end
    assert t.token() == "t5" and calls[-1] == ("POST", "app/installations/42/access_tokens")


def test_not_installed_on_its_owner(monkeypatch):
    monkeypatch.setattr(github_app, "call", lambda m, p, b: {"owner": {"login": "someone"}} if p == "app" else installations(3))
    with pytest.raises(github.ChangeError, match="not installed on its owner's account"):
        github_app.Installation("123", PEM).token()


def test_from_env(tmp_path):
    (tmp_path / "key.pem").write_text(PEM)
    t = github_app.from_env({"NOLLI_GITHUB_APP_ID": "123", "NOLLI_GITHUB_APP_PRIVATE_KEY_FILE": str(tmp_path / "key.pem"),
                             "NOLLI_GITHUB_APP_INSTALLATION_ID": "42"})
    assert isinstance(t, github_app.Installation) and t.pem == PEM and t.installation == "42"
    one_line = github_app.from_env({"NOLLI_GITHUB_APP_ID": "123", "NOLLI_GITHUB_APP_PRIVATE_KEY": PEM.replace("\n", "\\n")})
    assert one_line.pem == PEM  # a key pasted into a variable with its newlines escaped
    assert github_app.from_env({"NOLLI_GITHUB_TOKEN": "x"}).token() == "x"
    assert github_app.from_env({}).token() is None


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
    site = hosted.Site(tmp_path, token=github_app.Fixed("tok"))
    with pytest.raises(github.NotFound):
        site.plan("me", "secret")
    assert site.ask("repos/o/pub")["full_name"] == "o/pub"
    assert asked[-2:] == [("repos/o/pub", "tok"), ("repos/o/pub", None)]  # the app is not installed there


def test_a_token_that_cannot_be_had_falls_back_to_nobody(tmp_path, monkeypatch, capsys):
    class Broken(github_app.Token):
        def token(self):
            raise github.ChangeError("GitHub App: POST app/installations/1/access_tokens: 401 Unauthorized")

    monkeypatch.setattr(hosted.github, "api", lambda path, tok: {"tok": tok})
    site = hosted.Site(tmp_path, token=Broken())
    assert site.ask("x") == {"tok": None} and site.ask("y") == {"tok": None}
    assert capsys.readouterr().err.count("asking as nobody") == 1
