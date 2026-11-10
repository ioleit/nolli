"""The keys that identify a model."""

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
    def from_remote(cls, remote: str) -> Repo:
        """From a remote's URL as git or nolli spells it (``https://github.com/o/r``, ``git@github.com:o/r.git``)."""
        m = GITHUB_REMOTE.match(remote)
        return cls(f"{m.group(1)}/{m.group(2)}/{m.group(3)}") if m else cls(remote)

    def make_commit(self, sha: str) -> CommitKey:
        return CommitKey(self, sha)

    def make_change(self, base: str, head: str) -> ChangeKey:
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


class ModelKey:
    """What a model is the model of: a repository at one commit (``CommitKey``), the change
    between two of its commits (``ChangeKey``), or anything else, by name (``StringKey``)."""

    repo: Repo

    def format_name(self) -> str:
        raise NotImplementedError

    def format_key(self) -> str:
        """The key as text: ``Model.key``, which a model's maps are kept by."""
        raise NotImplementedError

    def to_dict(self) -> dict:
        """The key as JSON (``key.json``)."""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: dict) -> ModelKey:
        if "key" in d:
            return StringKey(d["key"])
        repo = Repo(d["repo"])
        return repo.make_change(d["base"], d["head"]) if d.get("base") else repo.make_commit(d["head"])


@dataclass(frozen=True)
class CommitKey(ModelKey):
    """A repository at one commit."""

    repo: Repo
    sha: str  # full

    def make_change_to(self, head: str) -> ChangeKey:
        """The change from this commit to ``head``, in the same repository."""
        return ChangeKey(self.repo, self.sha, head)

    def format_name(self) -> str:
        return f"{self.repo.format_slug()}-{self.sha[:7]}"

    def format_key(self) -> str:
        return f"{self.repo.url}@{self.sha}"

    def to_dict(self) -> dict:
        return {"repo": self.repo.url, "head": self.sha}


@dataclass(frozen=True)
class ChangeKey(ModelKey):
    """The change between two commits of one repository."""

    repo: Repo
    base: str  # full
    head: str  # full

    def list_sides(self) -> tuple[CommitKey, CommitKey]:
        return self.repo.make_commit(self.base), self.repo.make_commit(self.head)

    def format_name(self) -> str:
        return f"{self.repo.format_slug()}-{self.base[:7]}-{self.head[:7]}"

    def format_key(self) -> str:
        return f"{self.repo.url}@{self.base}..{self.head}"

    def to_dict(self) -> dict:
        return {"repo": self.repo.url, "head": self.head, "base": self.base}


@dataclass(frozen=True)
class StringKey(ModelKey):
    """A model of no repository, by an arbitrary name: one a transformation made (a selection,
    a projection), one read from ``model.json`` alone, one built in a test."""

    text: str = ""

    def format_name(self) -> str:
        return self.text

    def format_key(self) -> str:
        return self.text

    def to_dict(self) -> dict:
        return {"key": self.text}
