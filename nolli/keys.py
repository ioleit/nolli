"""What a model is the model of: its key. A repository at one commit (``Commit``), or the
change between two commits of one repository (``ChangeKey``). Stores keep models by key
(``nolli/store.py``), sources fetch the commits a key names (``nolli/sources/``), and a
model's ``key`` (``Model.key``) is its key as text, which stored overlays are kept by."""

from __future__ import annotations

import re
from dataclasses import dataclass

GITHUB_REMOTE = re.compile(r"^(?:https://(?:[^@/]+@)?|ssh://git@|git@)?(github\.com)[/:]([^/]+)/(.+?)(?:\.git)?/?$")


class NotAllowed(Exception):
    """The current request may not read this repository. A server answers 404, as for one
    that does not exist."""


@dataclass(frozen=True)
class Repo:
    """A repository, by where it comes from: ``github.com/<owner>/<repo>`` for one on GitHub,
    else the path of a local repository (``/home/me/src/shop``)."""

    url: str

    @classmethod
    def from_remote(cls, remote: str) -> "Repo":
        """From a remote's URL as git or nolli spells it (``https://github.com/o/r``, ``git@github.com:o/r.git``)."""
        m = GITHUB_REMOTE.match(remote)
        return cls(f"{m.group(1)}/{m.group(2)}/{m.group(3)}") if m else cls(remote)

    def make_commit(self, sha: str) -> "Commit":
        return Commit(self, sha)

    def make_change(self, base: str, head: str) -> "ChangeKey":
        return ChangeKey(self, base, head)

    def format_slug(self) -> str:
        """A short name: ``owner_repo`` on GitHub, else the directory's name."""
        parts = self.url.rstrip("/").split("/")
        return f"{parts[1]}_{parts[2]}" if parts[0] == "github.com" and len(parts) == 3 else parts[-1]

    def format_remote(self) -> str | None:
        """Where git fetches it from: ``https://github.com/<owner>/<repo>``, or None for a local one."""
        return f"https://{self.url}" if self.url.startswith("github.com/") else None

    def format_name(self) -> str:
        """The repository's own name, ``repo`` of ``github.com/owner/repo``."""
        return self.url.rstrip("/").split("/")[-1]


@dataclass(frozen=True)
class Commit:
    """A repository at one commit."""

    repo: Repo
    sha: str  # full

    def make_change_to(self, head: str) -> "ChangeKey":
        """The change from this commit to ``head``, in the same repository."""
        return ChangeKey(self.repo, self.sha, head)

    def format_name(self) -> str:
        return f"{self.repo.format_slug()}-{self.sha[:7]}"

    def format_key(self) -> str:
        return f"{self.repo.url}@{self.sha}"


@dataclass(frozen=True)
class ChangeKey:
    """The change between two commits of one repository."""

    repo: Repo
    base: str  # full
    head: str  # full

    def list_sides(self) -> tuple[Commit, Commit]:
        return self.repo.make_commit(self.base), self.repo.make_commit(self.head)

    def format_name(self) -> str:
        return f"{self.repo.format_slug()}-{self.base[:7]}-{self.head[:7]}"

    def format_key(self) -> str:
        return f"{self.repo.url}@{self.base}..{self.head}"


ModelKey = Commit | ChangeKey


def dump_key(key: ModelKey) -> dict:
    """The key as JSON (``key.json``)."""
    return {"repo": key.repo.url, "head": key.head, "base": key.base} if isinstance(key, ChangeKey) else \
        {"repo": key.repo.url, "head": key.sha}


def parse_key(d: dict) -> ModelKey:
    repo = Repo(d["repo"])
    return repo.make_change(d["base"], d["head"]) if d.get("base") else repo.make_commit(d["head"])
