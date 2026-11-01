"""The token ``nolli serve`` asks GitHub's API with: a GitHub App's installation token, or a
token given as it is, or none (60 requests an hour for the whole server).

From the environment (from_env()):

- ``NOLLI_GITHUB_APP_ID`` and ``NOLLI_GITHUB_APP_PRIVATE_KEY`` (the PEM itself) or
  ``NOLLI_GITHUB_APP_PRIVATE_KEY_FILE``, and optionally ``NOLLI_GITHUB_APP_INSTALLATION_ID``
  (default: the app's only installation): an installation token, made when needed (a JWT
  signed with the key, exchanged at ``/app/installations/<id>/access_tokens``) and renewed
  before it expires after an hour. Needs ``cryptography`` (``pip install 'nolli-review[serve]'``).
- ``NOLLI_GITHUB_TOKEN``: used as it is.

The token is for the API only; clones stay anonymous. A token may see private repositories
(those the app is installed on): the server refuses whatever GitHub says is private.
"""

from __future__ import annotations

import base64
import calendar
import json
import os
import threading
import time
import urllib.error
import urllib.request

from .change import ChangeError

API = "https://api.github.com"
RENEW = 300  # s before an installation token expires that a new one is made


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt(app_id: str, pem: str, now: float | None = None) -> str:
    """The app's JWT (RS256), valid for nine minutes, from a minute ago against clock drift."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(now if now is not None else time.time())
    head = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr, call-arg]
    return f"{head}.{body}.{b64url(sig)}"


def call(method: str, path: str, bearer: str) -> dict | list:
    req = urllib.request.Request(f"{API}/{path}", method=method, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nolli",
        "Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        raise ChangeError(f"GitHub App: {method} {path}: {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise ChangeError(f"GitHub App: {method} {path}: {exc.reason}") from None


class Token:
    """A token source: token() gives what to send, or None to ask as nobody."""

    def token(self) -> str | None:
        return None

    def describe(self) -> str:
        return "no GitHub token: 60 API requests an hour"


class Fixed(Token):
    def __init__(self, value: str):
        self.value = value

    def token(self) -> str | None:
        return self.value

    def describe(self) -> str:
        return "GitHub token from NOLLI_GITHUB_TOKEN"


class Installation(Token):
    """A GitHub App's installation token, renewed RENEW seconds before it expires."""

    def __init__(self, app_id: str, pem: str, installation: str | None = None):
        self.app_id, self.pem, self.installation = app_id, pem, installation
        self.value, self.expires = "", 0.0
        self.lock = threading.Lock()

    def token(self) -> str | None:
        with self.lock:
            if self.value and time.time() < self.expires - RENEW:
                return self.value
            app = jwt(self.app_id, self.pem)
            if not self.installation:
                found = call("GET", "app/installations", app)
                if len(found) != 1:
                    raise ChangeError(f"GitHub App {self.app_id} has {len(found)} installations: "
                                      "set NOLLI_GITHUB_APP_INSTALLATION_ID to the one to use")
                self.installation = str(found[0]["id"])  # type: ignore[index]
            out = call("POST", f"app/installations/{self.installation}/access_tokens", app)
            assert isinstance(out, dict)
            self.value = out["token"]
            self.expires = calendar.timegm(time.strptime(out["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
            return self.value

    def describe(self) -> str:
        return f"GitHub App {self.app_id}" + (f", installation {self.installation}" if self.installation else "")


def from_env(env: dict | None = None) -> Token:
    env = os.environ if env is None else env
    app_id = env.get("NOLLI_GITHUB_APP_ID")
    pem = env.get("NOLLI_GITHUB_APP_PRIVATE_KEY")
    if not pem and env.get("NOLLI_GITHUB_APP_PRIVATE_KEY_FILE"):
        with open(os.path.expanduser(env["NOLLI_GITHUB_APP_PRIVATE_KEY_FILE"])) as f:
            pem = f.read()
    if app_id and pem:
        return Installation(app_id, pem.replace("\\n", "\n"), env.get("NOLLI_GITHUB_APP_INSTALLATION_ID") or None)
    if env.get("NOLLI_GITHUB_TOKEN"):
        return Fixed(env["NOLLI_GITHUB_TOKEN"])
    return Token()
