"""Where nolli keeps what it builds, and where it gets the commits it builds from.

A model is the model of something: a repository at one commit (``Commit``), or the change
between two commits of one repository (``Change``). That is its key (``ModelKey``). Two
interfaces stand between the code that builds and shows models and where they are kept:

- ``ModelStore``: models and their reviews, by key. nolli's own is ``DirectoryStore``.
- ``Sources``: a local git repository that has the commits a build needs. nolli's own are
  ``LocalRepository`` (a checkout you have, ``nolli view``) and ``CloneCache`` (bare clones
  fetched from their remotes, ``nolli serve``).

A service built on nolli can bring its own of either, for example one that keeps models
elsewhere or decides per request who may read what (and raises ``NotAllowed`` for the rest).

This module depends on the data model only. What is made from a model (a review's diagram
and brief, a report) is nolli/derived.py; whoever needs it loads the model and makes it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .model import Model

# -- keys ------------------------------------------------------------------


@dataclass(frozen=True)
class Repo:
    """A repository, by where it comes from: ``github.com/<owner>/<repo>`` for one on GitHub,
    else the path of a local repository (``/home/me/src/shop``)."""

    url: str

    @classmethod
    def from_remote(cls, remote: str) -> Repo:
        """From a remote's URL as git or nolli spells it (``https://github.com/o/r``, ``git@github.com:o/r.git``)."""
        m = re.match(r"^(?:https://(?:[^@/]+@)?|ssh://git@|git@)?(github\.com)[/:]([^/]+)/(.+?)(?:\.git)?/?$", remote)
        return cls(f"{m.group(1)}/{m.group(2)}/{m.group(3)}") if m else cls(remote)

    def make_commit(self, sha: str) -> Commit:
        return Commit(self, sha)

    def make_change(self, base: str, head: str) -> Change:
        return Change(self, base, head)

    def format_slug(self) -> str:
        """A short name: ``owner_repo`` on GitHub, else the directory's name."""
        parts = self.url.rstrip("/").split("/")
        return f"{parts[1]}_{parts[2]}" if parts[0] == "github.com" and len(parts) == 3 else parts[-1]

    def format_remote(self) -> str | None:
        """Where git fetches it from: ``https://github.com/<owner>/<repo>``, or None for a local one."""
        return f"https://{self.url}" if self.url.startswith("github.com/") else None


@dataclass(frozen=True)
class Commit:
    """A repository at one commit."""

    repo: Repo
    sha: str  # full

    def make_change_to(self, head: str) -> Change:
        """The change from this commit to ``head``, in the same repository."""
        return Change(self.repo, self.sha, head)

    def format_name(self) -> str:
        return f"{self.repo.format_slug()}-{self.sha[:7]}"


@dataclass(frozen=True)
class Change:
    """The change between two commits of one repository."""

    repo: Repo
    base: str  # full
    head: str  # full

    def list_sides(self) -> tuple[Commit, Commit]:
        return self.repo.make_commit(self.base), self.repo.make_commit(self.head)

    def format_name(self) -> str:
        return f"{self.repo.format_slug()}-{self.base[:7]}-{self.head[:7]}"


ModelKey = Commit | Change


def format_key(key: ModelKey) -> dict:
    """The key as JSON, as a model records it (``meta["key"]``)."""
    return {"repo": key.repo.url, "head": key.head, "base": key.base} if isinstance(key, Change) else \
        {"repo": key.repo.url, "head": key.sha}


def parse_key(d: dict) -> ModelKey:
    repo = Repo(d["repo"])
    return repo.make_change(d["base"], d["head"]) if d.get("base") else repo.make_commit(d["head"])


# -- what is kept ------------------------------------------------------------


@dataclass
class Review:
    """A review of a change: its verdicts on the review diagram's boxes and edges, and who made
    them (``{"by": "agent" | "ci" | "upload", "at": <ISO time>, ...}``).
    TODO: annotations on the model, once verdicts live there."""

    verdicts: list[dict]
    by: dict = field(default_factory=lambda: {"by": "unknown"})


class NotAllowed(Exception):
    """The current request may not read this repository. A server answers 404, as for one
    that does not exist."""


class FetchError(Exception):
    """The commits a build needs could not be had (not in the repository, or not fetched)."""


class ModelStore(Protocol):
    """Models and their reviews, by key."""

    def find_key(self, name: str) -> ModelKey | None:
        """The key of the stored model called ``name`` (``key.format_name()``, or a name it was
        stored under), None if there is none. URLs carry names."""

    def list_names(self) -> list[str]:
        """The names of the stored models this request may read, newest first (a list to pick
        from, as nolli view offers)."""

    def load_model(self, key: ModelKey) -> Model | None:
        """The stored model, None if there is none. Raises NotAllowed if this request may not
        read ``key.repo``."""

    def save_model(self, key: ModelKey, model: Model) -> None:
        """Keep ``model`` as the model of ``key``, replacing one kept before."""

    def load_review(self, key: ModelKey) -> Review | None:
        """The review of the model, None if it has none. Raises NotAllowed as load_model."""

    def save_review(self, key: ModelKey, review: Review) -> None:
        """Keep ``review`` as the model's review, replacing one kept before."""


class Sources(Protocol):
    """Local git repositories to build from."""

    def open_commits(self, repo: Repo, *shas: str) -> AbstractContextManager[Path]:
        """A local repository of ``repo`` that has ``shas`` (fetching what is missing), held
        while the ``with`` block uses it. Raises NotAllowed if this request may not read
        ``repo``, also when nothing needs fetching."""


# -- nolli's own: models in directories --------------------------------------


class DirectoryStore:
    """Models as directories under ``root``, one per model, named after its key: the model
    (``model.json``), its key (``key.json``, read without parsing the model) and its review
    (``verdicts.json`` + ``review.json``). Every directory under ``root`` with a model.json is a
    model, also one written elsewhere under a name of its own (``nolli diff -o``, a CI run's
    artifact); its key comes from its files (read_key())."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.index: dict[ModelKey, Path] = {}  # key -> its directory
        self.names: dict[str, ModelKey] = {}  # directory name -> key
        self.scanned = 0  # root's mtime (ns) when it was last read
        self.models: dict[Path, tuple[tuple[int, int], Model]] = {}  # model.json -> which file (inode, mtime), the model parsed
        self.loading: dict[Path, threading.Lock] = {}

    def locate(self, key: ModelKey) -> Path:
        """The model's directory (existing or not), for code that works with its files."""
        self.scan()
        return self.index.get(key) or self.root / key.format_name()

    def scan(self) -> None:
        """Read which directories hold which models, when ``root`` changed since the last time."""
        try:
            mtime = self.root.stat().st_mtime_ns
        except OSError:
            return
        with self.lock:
            if mtime == self.scanned:
                return
            self.scanned = mtime
            index, names = {}, {}
            for d in self.root.iterdir():
                if d.name.startswith(".") or not (d / "model.json").is_file():
                    continue
                key = read_key(d)
                if key is not None:
                    index.setdefault(key, d)
                    names[d.name] = key
            self.index, self.names = index, names

    def find_key(self, name: str) -> ModelKey | None:
        if not name or "/" in name or name.startswith("."):
            return None
        self.scan()
        if name not in self.names and (self.root / name / "model.json").is_file():
            self.scanned = 0  # written since: read again
            self.scan()
        return self.names.get(name)

    def list_names(self) -> list[str]:
        self.scan()

        def mtime(name: str) -> float:
            try:
                return (self.root / name / "model.json").stat().st_mtime
            except OSError:
                return 0.0

        return sorted(self.names, key=lambda n: -mtime(n))

    def load_model(self, key: ModelKey) -> Model | None:
        f = self.locate(key) / "model.json"
        with self.lock:
            lock = self.loading.setdefault(f, threading.Lock())
        with lock:  # a second request waits for a load under way
            try:
                st = f.stat()
            except OSError:
                return None
            version = (st.st_ino, st.st_mtime_ns)  # a model saved again is a new file
            hit = self.models.get(f)
            if hit and hit[0] == version:
                return hit[1]
            m = Model.load(f)
            m.roots()  # the containment index, built before threads share the model
            self.models[f] = (version, m)
            return m

    def save_model(self, key: ModelKey, model: Model) -> None:
        """Written next to the final directory, then swapped in whole; a review kept before stays."""
        model.meta["key"] = format_key(key)
        out = self.locate(key)
        tag = f"{os.getpid()}.{threading.get_ident()}"
        tmp, old = self.root / f".{out.name}.{tag}", self.root / f".{out.name}.old.{tag}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            model.save(tmp / "model.json")
            (tmp / "key.json").write_text(json.dumps(format_key(key)))
            for f in ("verdicts.json", "review.json"):  # the review stays with the model it answers
                if (out / f).is_file():
                    shutil.copy2(out / f, tmp / f)
            if out.exists():
                out.rename(old)
            tmp.rename(out)
            shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        with self.lock:
            self.index[key], self.names[out.name] = out, key

    def load_review(self, key: ModelKey) -> Review | None:
        d = self.locate(key)
        try:
            verdicts = json.loads((d / "verdicts.json").read_text())
        except (OSError, ValueError):
            return None
        try:
            by = json.loads((d / "review.json").read_text())
        except (OSError, ValueError):
            by = {"by": "unknown"}  # verdicts from before review.json existed
        return Review(verdicts if isinstance(verdicts, list) else [], by)

    def save_review(self, key: ModelKey, review: Review) -> None:
        d = self.locate(key)
        (d / "verdicts.json").write_text(json.dumps([{k: v[k] for k in ("target", "verdict", "reason")} for v in review.verdicts], indent=1))
        (d / "review.json").write_text(json.dumps(review.by, indent=1))


def read_key(d: Path) -> ModelKey | None:
    """The key of a model directory: its key.json; else what its meta records (report.json's, as
    nolli diff -o and nolli before 0.7 wrote it, smaller than model.json; else model.json's),
    for a model from before keys were recorded what the meta says about its commits."""
    try:
        return parse_key(json.loads((d / "key.json").read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    meta = None
    for f in ("report.json", "model.json"):
        try:
            meta = json.loads((d / f).read_text()).get("meta", {})
            break
        except (OSError, ValueError):
            continue
    if meta is None:
        return None
    if isinstance(meta.get("key"), dict):
        try:
            return parse_key(meta["key"])
        except (KeyError, TypeError):
            return None
    diff = meta.get("diff") or {}
    repo = Repo.from_remote(diff["remote"]) if diff.get("remote") else Repo(str(meta.get("repo") or d.name))
    if diff.get("base") and diff.get("head"):
        return repo.make_change(diff["base"], diff["head"])
    return repo.make_commit(str(meta.get("commit") or d.name))


# -- nolli's own: local repositories -----------------------------------------


def run_git(repo: Path, *args: str, env: dict | None = None) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)
    if r.returncode:
        raise FetchError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout


def has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, check=False).returncode == 0


class LocalRepository:
    """A repository you have checked out: every commit a build needs is in it already."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextlib.contextmanager
    def open_commits(self, repo: Repo, *shas: str) -> Iterator[Path]:
        missing = [s for s in shas if not has_commit(self.path, s)]
        if missing:
            raise FetchError(f"{self.path} has no commit {', '.join(s[:7] for s in missing)}")
        yield self.path


@contextlib.contextmanager
def lock_clone(clone: Path) -> Iterator[None]:
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
            with lock_clone(path):
                if not (path / "HEAD").is_file():
                    run_git(path, "init", "-q", "--bare")
                    run_git(path, "remote", "add", "origin", remote)
                os.utime(path)  # used now (retention)
                missing = [s for s in shas if not has_commit(path, s)]
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
            run_git(clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *shas, env=env)
        except FetchError as exc:
            raise FetchError(f"could not fetch {', '.join(s[:7] for s in shas)} from {repo.url}: {exc}") from None
