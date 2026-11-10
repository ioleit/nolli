"""Gives access to a repository's commits, as local git clones.

It defines the interface, ``Sources``, and two kinds of it: a checkout you have, and bare
clones fetched from their remotes."""

from __future__ import annotations

import contextlib
import fcntl
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from nolli.logic.sources import git
from nolli.logic.sources.git import GitError
from nolli.model.keys import NotAllowed, Repo  # noqa: F401 (part of the protocol)


class FetchError(GitError):
    """The commits a build needs could not be had (not in the repository, or not fetched)."""


class Sources(Protocol):
    """Local git repositories to build from. An implementation may decide per request who may
    read what, and raise ``NotAllowed`` for the rest."""

    def open_commits(self, repo: Repo, *shas: str) -> AbstractContextManager[Path]:
        """A local repository of ``repo`` that has ``shas`` (fetching what is missing), held
        while the ``with`` block uses it. Raises NotAllowed if this request may not read
        ``repo``, also when nothing needs fetching."""


class LocalRepository:
    """A repository you have checked out: every commit a build needs is in it already."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextlib.contextmanager
    def open_commits(self, repo: Repo, *shas: str) -> Iterator[Path]:
        missing = [s for s in shas if not git.has_commit(self.path, s)]
        if missing:
            raise FetchError(f"{self.path} has no commit {', '.join(s[:7] for s in missing)}")
        yield self.path


@contextlib.contextmanager
def _lock_clone(clone: Path) -> Iterator[None]:
    """One git command at a time in a clone, across processes."""
    with open(clone / "nolli.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class CloneCache:
    """Bare clones under ``root`` (``<root>/<owner>/<repo>``), fetched from their remotes as
    builds need commits. git runs with ``token(owner, repo)`` if it gives one (as an HTTP
    header, never stored in the clone), else with no credentials."""

    def __init__(self, root: Path, token: Callable[[str, str], str | None] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.held: dict[Path, int] = {}  # clones in use, how many times (retention leaves them)
        self.lock = threading.Lock()

    def locate(self, repo: Repo) -> Path:
        owner, name = repo.url.rstrip("/").split("/")[-2:]
        return self.root / owner.lower() / name.lower()

    def list_held(self) -> set[Path]:
        with self.lock:
            return set(self.held)

    @contextlib.contextmanager
    def open_commits(self, repo: Repo, *shas: str) -> Iterator[Path]:
        remote = repo.format_remote()
        if remote is None:
            raise ValueError(f"no remote to fetch {repo.url} from")
        path = self.locate(repo)
        with self.lock:
            self.held[path] = self.held.get(path, 0) + 1
        try:
            path.mkdir(parents=True, exist_ok=True)
            with _lock_clone(path):
                if not (path / "HEAD").is_file():
                    git.run_git(path, "init", "-q", "--bare")
                    git.run_git(path, "remote", "add", "origin", remote)
                os.utime(path)  # used now (retention)
                missing = [s for s in shas if not git.has_commit(path, s)]
                if missing:
                    self.fetch_commits(repo, path, missing)
            yield path
        finally:
            with self.lock:
                self.held[path] -= 1
                if not self.held[path]:
                    del self.held[path]

    def fetch_commits(self, repo: Repo, clone: Path, shas: list[str]) -> None:
        """git fetch, with the token in git's environment (not its arguments, which other
        processes can read)."""
        import base64

        owner, name = repo.url.rstrip("/").split("/")[-2:]
        tok = self.token(owner, name) if self.token else None
        env = dict(os.environ)
        if tok:
            basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                       GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
        try:
            git.run_git(clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *shas, env=env)
        except GitError as exc:
            raise FetchError(f"could not fetch {', '.join(s[:7] for s in shas)} from {repo.url}: {exc}") from None


class LocalFirst:
    """A repository you have checked out (if any), and clones for the rest: the checkout when it
    has the commits a build needs (yours, or ones fetched into it), else ``clones``."""

    def __init__(self, path: Path | None, clones: CloneCache):
        self.path, self.clones = (Path(path) if path else None), clones

    @contextlib.contextmanager
    def open_commits(self, repo: Repo, *shas: str) -> Iterator[Path]:
        if self.path is not None and all(git.has_commit(self.path, s) for s in shas):
            yield self.path
            return
        with self.clones.open_commits(repo, *shas) as clone:
            yield clone
