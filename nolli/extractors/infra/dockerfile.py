"""Deployment: what goes into each image, from Dockerfiles.

- ``COPY``/``ADD`` sources that exist in the repository are ``allocate``
  directory/file -> image (subkind copies). Sources are relative to the build
  context (from compose if known, else the Dockerfile's directory).
- ``CMD``/``ENTRYPOINT`` naming a Python module (``python -m a.b``,
  ``uvicorn a.b:app``) or a script file are ``use`` image -> file
  (subkind entrypoint).
Images not referenced by any compose file are created here as well.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

from ...model import ALLOCATE, CONTAIN, USE, Entity, Provenance, Relation, address
from .. import Context

PY_MODULE = re.compile(r"(?:python[\d.]*\s+-m\s+|uvicorn\s+|gunicorn\s+)([A-Za-z_][\w.]*)(?::\w+)?")
SCRIPT = re.compile(r"(?:^|\s)((?:[\w./-]+/)?[\w-]+\.(?:py|sh|js|mjs|ts))(?=\s|$)")


def instructions(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        parts = buf.split(None, 1)
        if parts:
            out.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
        buf = ""
    return out


def run(ctx: Context) -> None:
    m = ctx.model
    for f in ctx.files:
        rel = ctx.rel(f)
        name = PurePosixPath(rel).name
        if not (name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")):
            continue
        group = m.ensure_root(address.PKG, "dockerfile")
        iid = address.built(rel)  # the image, by its Dockerfile
        if not m.has(iid):
            m.add_entity(Entity(iid, "image", rel, {"dockerfile": rel, "context": str(PurePosixPath(rel).parent)}, Provenance("dockerfile", rel)))
            m.add_relation(Relation(CONTAIN, group, iid, provenance=Provenance("dockerfile", rel)))
        context = m.entities[iid].attrs.get("context", ".")
        ctx_dir = PurePosixPath("." if context in ("", ".") else context)
        text = ctx.read(rel).decode("utf-8", "replace")
        for lineno, (ins, arg) in enumerate(instructions(text), 1):
            if ins in ("COPY", "ADD"):
                try:
                    toks = shlex.split(arg)
                except ValueError:
                    toks = arg.split()
                if any(t.startswith("--from") for t in toks):
                    continue
                toks = [t for t in toks if not t.startswith("--")]
                if len(toks) < 2:
                    continue
                for src in toks[:-1]:
                    p = PurePosixPath(src.rstrip("/"))
                    full = (ctx_dir / p) if str(p) not in (".", "") else ctx_dir
                    full_s = str(full) if str(full) not in ("", ".") else "."
                    full_s = full_s[2:] if full_s.startswith("./") else full_s
                    fid = ctx.repo_id(full_s)
                    if m.has(fid):
                        m.add_relation(Relation(ALLOCATE, fid, iid, "copies", provenance=Provenance("dockerfile", rel, lineno)))
                    else:
                        ctx.notes.setdefault("dockerfile-unmatched-copy", []).append((rel, src))
            elif ins in ("CMD", "ENTRYPOINT"):
                cmd = arg
                if arg.startswith("["):
                    try:
                        import json

                        cmd = " ".join(json.loads(arg))
                    except json.JSONDecodeError:
                        pass
                for mod in PY_MODULE.findall(cmd):
                    cand = mod.replace(".", "/")
                    for fid in (ctx.repo_id(f"{cand}.py"), ctx.repo_id(f"{cand}/__init__.py"), ctx.repo_id(f"{cand}/__main__.py")):
                        if m.has(fid):
                            m.add_relation(Relation(USE, iid, fid, "entrypoint", provenance=Provenance("dockerfile", rel, lineno)))
                            break
                for script in SCRIPT.findall(cmd):
                    fid = ctx.repo_id(str(ctx_dir / script).removeprefix("./"))
                    if m.has(fid):
                        m.add_relation(Relation(USE, iid, fid, "entrypoint", provenance=Provenance("dockerfile", rel, lineno)))
