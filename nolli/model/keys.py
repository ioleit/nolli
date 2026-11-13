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
        """Return a short name for the repository, ``owner_repo`` for one on GitHub and the
        directory's name otherwise."""
        parts = self.url.rstrip("/").split("/")
        return f"{parts[1]}_{parts[2]}" if parts[0] == "github.com" and len(parts) == 3 else parts[-1]

    def format_remote(self) -> str | None:
        """Where git fetches it from: ``https://github.com/<owner>/<repo>``, or None for a local one."""
        return f"https://{self.url}" if self.url.startswith("github.com/") else None

    def format_name(self) -> str:
        """Return the repository's own name, the last part of its URL: ``repo`` for ``github.com/o/repo``."""
        return self.url.rstrip("/").split("/")[-1]


PROJECTED = "~project:"  # between a projected model's source key and which projection it is
SELECTED = "~select:"  # between a selected part's source key and which part it is
NAMED = "@~"  # between a StringKey's repository and its text


class ModelKey:
    """What a model is the model of: a repository at one commit (``CommitKey``), the change
    between two of its commits (``ChangeKey``), a projection of another model
    (``ProjectionKey``), a part of another model (``SelectionKey``), or anything else, by name
    (``StringKey``)."""

    repo: Repo

    def format_key(self) -> str:
        """Return the key as text. A model's maps are kept by the text of its ``Model.key``, the
        viewer's API names a model by it, and ``parse`` reads it back."""
        raise NotImplementedError

    @classmethod
    def parse(cls, text: str) -> ModelKey:
        """Parse ``text`` as ``format_key()`` writes it: ``<source>~project:<spec>`` gives a
        ProjectionKey, ``<source>~select:<digest>`` a SelectionKey, ``<repo>@~<text>`` a StringKey
        of that repository, ``<repo>@<sha>`` a CommitKey, ``<repo>@<base>..<head>`` a ChangeKey,
        and text without an ``@`` a StringKey without one. Every key reads back as it was (a
        StringKey's text has no ``@``, ``~project:`` or ``~select:``). Raise ValueError for text
        with an ``@`` that is none of these (``x@``, ``@abc``)."""
        at = max(text.rfind(PROJECTED), text.rfind(SELECTED))  # the last step made it
        if at > 0:
            source, rest = ModelKey.parse(text[:at]), text[at:]
            if rest.startswith(PROJECTED) and rest[len(PROJECTED):]:
                return ProjectionKey(source, rest[len(PROJECTED):])
            if rest.startswith(SELECTED) and rest[len(SELECTED):]:
                return SelectionKey(source, rest[len(SELECTED):])
        repo, named, rest = text.partition(NAMED)
        if named and repo:
            return StringKey(rest, Repo(repo))
        repo, at, rest = text.rpartition("@")
        if not at:
            return StringKey(text)
        if not repo or not rest:
            raise ValueError(f"no model's key: {text!r}")
        base, dots, head = rest.partition("..")
        return Repo(repo).make_change(base, head) if dots else Repo(repo).make_commit(rest)

    def to_dict(self) -> dict:
        """Return the key as a JSON-ready dict, as it is written to ``key.json``."""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: dict) -> ModelKey:
        if "projection" in d:
            return ProjectionKey(ModelKey.from_dict(d["source"]), d["projection"])
        if "selection" in d:
            return SelectionKey(ModelKey.from_dict(d["source"]), d["selection"])
        if "key" in d:
            return StringKey(d["key"], Repo(d["repo"]) if d.get("repo") else None)
        repo = Repo(d["repo"])
        return repo.make_change(d["base"], d["head"]) if d.get("base") else repo.make_commit(d["head"])


@dataclass(frozen=True)
class CommitKey(ModelKey):
    """A repository at one commit."""

    repo: Repo
    sha: str  # full

    def make_change_to(self, head: str) -> ChangeKey:
        """Return the key of the change from this commit to ``head`` in the same repository."""
        return ChangeKey(self.repo, self.sha, head)

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

    def format_key(self) -> str:
        return f"{self.repo.url}@{self.base}..{self.head}"

    def to_dict(self) -> dict:
        return {"repo": self.repo.url, "head": self.head, "base": self.base}


@dataclass(frozen=True)
class StringKey(ModelKey):
    """An adhoc key, for a model made in code (a test's, a custom transformation's). ``repo`` is
    the repository whose model it was made from, if any: a store bound to one repository keeps
    only keys of that repository (``nolli.store.repo_directory``), so a key without one can only
    be kept in memory (``MemoryStore``)."""

    text: str = ""
    repo: Repo | None = None  # type: ignore[assignment]  # None: a model of no repository

    def __post_init__(self) -> None:
        if "@" in self.text or PROJECTED in self.text or SELECTED in self.text:
            raise ValueError(f"a StringKey's text has no @ (a commit's or a repository's mark), {PROJECTED} or {SELECTED} "
                             f"(a step made on a model): {self.text!r}")

    def format_key(self) -> str:
        return f"{self.repo.url}{NAMED}{self.text}" if self.repo else self.text

    def to_dict(self) -> dict:
        return {"key": self.text, **({"repo": self.repo.url} if self.repo else {})}


@dataclass(frozen=True)
class ProjectionKey(ModelKey):
    """A projection of another model: the source's key and which projection it is, the
    projection's spec as text (``ProjectionSpec.format``, e.g. ``p=default``). The same spec of
    the same model always draws the same, so the key is known before projecting
    (``projection.make_key``), and what is kept for it is found again when it is drawn again."""

    source: ModelKey
    spec: str  # ProjectionSpec.format()

    @property
    def repo(self) -> Repo:  # type: ignore[override]
        """The source's repository (None for a ``StringKey`` source without one)."""
        return self.source.repo

    def format_key(self) -> str:
        return f"{self.source.format_key()}{PROJECTED}{self.spec}"

    def to_dict(self) -> dict:
        return {"source": self.source.to_dict(), "projection": self.spec}


@dataclass(frozen=True)
class SelectionKey(ModelKey):
    """A part of another model (``select``): the source's key and which part it is, a digest of
    the ids of the nodes and edges kept. The same part of the same model
    has the same key, so what is kept for it is found again when it is selected again."""

    source: ModelKey
    selection: str  # a digest of the kept ids

    @property
    def repo(self) -> Repo:  # type: ignore[override]
        """The source's repository (None for a ``StringKey`` source without one)."""
        return self.source.repo

    def format_key(self) -> str:
        return f"{self.source.format_key()}{SELECTED}{self.selection}"

    def to_dict(self) -> dict:
        return {"source": self.source.to_dict(), "selection": self.selection}


def find_base_key(key: ModelKey) -> ModelKey:
    """Return the key of the model that ``key`` is a view of. Strip projections and selections,
    which draw the same addresses as their source, until a key of a model of its own is left: a
    commit's, a change's or a named one. A change is not a view of its two commits: what is said
    about it, or about a projection of it, is about what changed."""
    while isinstance(key, (ProjectionKey, SelectionKey)):
        key = key.source
    return key
