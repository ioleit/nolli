"""Two commits of one repository, merged into one model that records what changed.

First version; the shape of the change records is a guess at what a viewer, a review
agent and GitHub links will need.

Each side is materialised with ``git archive`` into a temporary directory and extracted
as usual; the repository itself is not touched. The two models are then merged:

- an entity or relation present on one side only is ``added`` or ``removed``; a relation
  on both sides whose reference count differs is ``changed``;
- a file's content change comes from ``git diff -M -U0``: git status (A/M/D/R), old path
  for renames, added and removed line counts, and the hunks with the line numbers of every
  changed line;
- a class gets the changed lines that fall inside its line range (head lines from its head
  range, removed lines from its base range).

Records live in ``attrs["diff"]`` and are present only on what changed; everything else
is unchanged. Directories carry only a structural status; their line counts are summed at
view time. ``meta["diff"]`` keeps both commits and the GitHub remote, for links.
"""

from __future__ import annotations

import io
import re
import subprocess
import tarfile
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .model import CONTAIN, SYMBOL_KINDS, Entity, Model, Relation, address

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileChange:
    git: str  # A, M, D, R
    path: str  # head path (base path for deletions)
    old_path: str | None = None  # base path, for renames
    binary: bool = False
    hunks: list[dict] = field(default_factory=list)  # {old: [start, len], new: [start, len], removed: [lines], added: [lines]}

    @property
    def added(self) -> int:
        return sum(len(h["added"]) for h in self.hunks)

    @property
    def removed(self) -> int:
        return sum(len(h["removed"]) for h in self.hunks)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def resolve(repo: Path, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def github_remote(repo: Path) -> str | None:
    """https://github.com/<owner>/<repo> for an origin on GitHub, else None."""
    try:
        url = git(repo, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        return None
    m = re.match(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$", url)
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def materialise(repo: Path, sha: str, dest: Path) -> None:
    """The tracked files of commit ``sha`` under ``dest``, without touching the repository."""
    data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")


def file_changes(repo: Path, base: str, head: str) -> dict[str, FileChange]:
    """Changed files keyed by head path (base path for deletions)."""
    out = git(repo, "diff", "-M", "-U0", "--no-color", "--no-ext-diff", base, head)
    changes: dict[str, FileChange] = {}
    cur: FileChange | None = None
    hunk: dict | None = None
    old_line = new_line = 0
    for line in out.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"^diff --git a/(.*) b/(.*)$", line)
            a, b = (m.group(1), m.group(2)) if m else (line, line)
            cur = FileChange("M", b, old_path=None)
            cur._a = a  # type: ignore[attr-defined]
            changes[b] = cur
            hunk = None
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.git = "A"
        elif line.startswith("deleted file mode"):
            cur.git = "D"
            changes.pop(cur.path, None)
            cur.path = cur._a  # type: ignore[attr-defined]
            changes[cur.path] = cur
        elif line.startswith("rename from "):
            cur.git = "R"
            cur.old_path = line[len("rename from "):]
        elif line.startswith("Binary files "):
            cur.binary = True
        elif line.startswith("@@"):
            m = HUNK.match(line)
            if not m:
                continue
            os_, ol, ns, nl = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
            hunk = {"old": [os_, ol], "new": [ns, nl], "removed": [], "added": []}
            cur.hunks.append(hunk)
            old_line, new_line = os_, ns
        elif hunk is not None and line.startswith("-") and not line.startswith("---"):
            hunk["removed"].append(old_line)
            old_line += 1
        elif hunk is not None and line.startswith("+") and not line.startswith("+++"):
            hunk["added"].append(new_line)
            new_line += 1
    for c in changes.values():
        c.__dict__.pop("_a", None)
    return changes


def relation_totals(m: Model) -> dict[tuple, tuple[int, Relation]]:
    """Non-containment relations aggregated by key: (total count, a representative)."""
    out: dict[tuple, tuple[int, Relation]] = {}
    for r in m.relations:
        if r.kind == CONTAIN:
            continue
        n, rep = out.get(r.key, (0, r))
        out[r.key] = (n + r.count, rep)
    return out


def merge(base: Model, head: Model, changes: dict[str, FileChange]) -> Model:
    m = Model()
    # entities: head's attributes win; removed ones keep base's
    for eid in sorted(set(base.entities) | set(head.entities)):
        src = head.entities.get(eid) or base.entities[eid]
        e = Entity(src.id, src.kind, src.name, dict(src.attrs), src.provenance)
        if eid not in base.entities:
            e.attrs["diff"] = {"status": "added"}
        elif eid not in head.entities:
            e.attrs["diff"] = {"status": "removed"}
        m.add_entity(e)
    # containment: the union (ids are path-based, so a shared entity has the same parent)
    seen = set()
    for side in (head, base):
        for r in side.relations:
            if r.kind == CONTAIN and (r.src, r.dst) not in seen and not (side is base and r.dst in head.entities):
                seen.add((r.src, r.dst))
                m.add_relation(Relation(CONTAIN, r.src, r.dst, provenance=r.provenance))
    # other relations: one per key, count from the side it is on
    tb, th = relation_totals(base), relation_totals(head)
    for key in sorted(set(tb) | set(th)):
        nb, rb = tb.get(key, (0, None))
        nh, rh = th.get(key, (0, None))
        rep = rh or rb
        attrs = dict(rep.attrs)
        if rb is None:
            attrs["diff"] = {"status": "added", "base": 0, "head": nh}
        elif rh is None:
            attrs["diff"] = {"status": "removed", "base": nb, "head": 0}
        elif nb != nh:
            attrs["diff"] = {"status": "changed", "base": nb, "head": nh}
        m.add_relation(Relation(rep.kind, rep.src, rep.dst, rep.subkind, nh or nb, rep.confidence, rep.provenance, attrs))
    # content: files, then classes inside them
    for path, c in changes.items():
        fid = address.repo(path)
        if not m.has(fid):
            continue  # a file nolli does not track (generated, ignored role)
        rec = m.entities[fid].attrs.setdefault("diff", {"status": "same"})
        rec.update({"git": c.git, "added": c.added, "removed": c.removed, "hunks": c.hunks})
        if c.old_path:
            rec["old_path"] = c.old_path
            old = address.repo(c.old_path)
            if m.has(old):
                m.entities[old].attrs.setdefault("diff", {"status": "removed"})["renamed_to"] = path
        if c.binary:
            rec["binary"] = True
        old_path = c.old_path or path
        for cid in m.children(fid) + ([] if not c.old_path else m.children(address.repo(c.old_path))):
            ce = m.entities[cid]
            if ce.kind not in SYMBOL_KINDS:
                continue
            hb, hh = base.entities.get(cid), head.entities.get(cid)
            added = [n for h in c.hunks for n in h["added"] if hh and hh.attrs.get("line", 0) <= n <= (hh.attrs.get("end_line") or 0)]
            removed = [n for h in c.hunks for n in h["removed"]
                       if hb and hb.attrs.get("path") == old_path and hb.attrs.get("line", 0) <= n <= (hb.attrs.get("end_line") or 0)]
            if added or removed:
                crec = ce.attrs.setdefault("diff", {"status": "same"})
                crec.update({"added": len(added), "removed": len(removed), "added_lines": added, "removed_lines": removed})
    return m


def diff(repo: Path, base_rev: str, head_rev: str, extract, progress=None) -> tuple[Model, dict]:
    """The merged model and a summary. ``extract(path, progress=…) -> (Model, Context)`` is
    ``nolli.build.extract``, passed in to keep this module free of the CLI; ``progress``
    (nolli/progress.py) reports each side's steps."""
    from .progress import Quiet

    progress = progress or Quiet()
    repo = repo.resolve()
    base, head = resolve(repo, base_rev), resolve(repo, head_rev)
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="nolli-diff-") as tmp:
        sides = {}
        for name, sha in (("base", base), ("head", head)):
            d = Path(tmp) / name
            d.mkdir()
            side = progress.under(name)
            with side.step("checking out the commit"):
                materialise(repo, sha, d)
            sides[name], _ctx = extract(d, progress=side)
    for side in sides.values():  # the root is the temporary directory; name it after the repository
        if side.has(address.REPO_ROOT):
            side.entities[address.REPO_ROOT].name = repo.name
    with progress.step("comparing the sides"):
        changes = file_changes(repo, base, head)
        m = merge(sides["base"], sides["head"], changes)
    by_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in m.entities.values():
        d = e.attrs.get("diff")
        if d:
            by_status["entities"][f"{e.kind}/{d['status']}"] += 1
            if (e.kind == "file" or e.kind in SYMBOL_KINDS) and (d.get("added") or d.get("removed")):
                by_status["content"][e.kind] += 1
    for r in m.relations:
        d = r.attrs.get("diff")
        if d:
            by_status["relations"][d["status"]] += 1
    m.meta = {
        "repo": repo.name, "commit": head[:7], "files": sides["head"].meta.get("files"),
        "languages": sides["base"].meta.get("languages", {}) | sides["head"].meta.get("languages", {}),
        "diff": {"base": base, "head": head, "base_short": base[:7], "head_short": head[:7], "remote": github_remote(repo),
                 "files_changed": len(changes), "added": sum(c.added for c in changes.values()),
                 "removed": sum(c.removed for c in changes.values())},
        "timings": {"total": round(time.time() - t0, 2)},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    summary = {k: dict(sorted(v.items())) for k, v in sorted(by_status.items())}
    summary["files_changed"] = len(changes)
    return m, summary
