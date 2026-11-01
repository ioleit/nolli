"""Deployment: services from docker-compose files.

- each service is a ``service`` in ``runtime:``, by the host name the other services
  reach it at (``runtime:db``);
- ``build`` gives an ``image`` in ``pkg:`` (``pkg:build/<Dockerfile>``) and ``allocate``
  image -> service ("runs as");
- ``depends_on`` is ``use`` service -> service (subkind depends_on);
- environment values that contain ``scheme://<host>`` where <host> is another
  service are ``use`` service -> service (subkind url, exact); other hosts
  become ``host`` entities in ``runtime:`` (external systems);
- every environment variable a service sets is ``provide`` service -> ``config:NAME``,
  so code that reads it (env-reads) is linked to where it runs.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import yaml

from ...model import ALLOCATE, CONTAIN, PROVIDE, USE, Entity, Provenance, Relation, address
from .. import Context
from ..interfaces import ensure_env, ensure_host

COMPOSE_NAMES = re.compile(r"(^|/)(docker-)?compose(\.[\w-]+)?\.ya?ml$")
URL_HOST = re.compile(r"[a-z][a-z0-9+.-]*://(?:[^@/\s]*@)?([A-Za-z0-9_.-]+)(?::\d+)?")
PERMISSION_KEY = re.compile(r"CORS|ORIGIN|ALLOWED|WHITELIST|TRUSTED", re.I)


def run(ctx: Context) -> None:
    m = ctx.model
    for f in ctx.files:
        rel = ctx.rel(f)
        if not COMPOSE_NAMES.search(rel):
            continue
        try:
            doc = yaml.safe_load(ctx.read(rel)) or {}
        except yaml.YAMLError:
            ctx.notes.setdefault("parse-errors", []).append(rel)
            continue
        services = doc.get("services") or {}
        if not isinstance(services, dict):
            continue
        group = m.ensure_root(address.RUNTIME, "compose")
        base = PurePosixPath(rel).parent
        svc_ids: dict[str, str] = {}
        for name, spec in services.items():
            sid = address.runtime(name)
            attrs = {"compose": rel}
            spec = spec or {}
            if isinstance(spec.get("image"), str):
                attrs["image"] = spec["image"]
            if spec.get("ports"):
                attrs["ports"] = [str(p) for p in spec["ports"]]
            m.add_entity(Entity(sid, "service", name, attrs, Provenance("compose", rel)))
            m.add_relation(Relation(CONTAIN, group, sid, provenance=Provenance("compose", rel)))
            svc_ids[name] = sid
        for name, spec in services.items():
            spec = spec or {}
            sid = svc_ids[name]
            build = spec.get("build")
            if build:
                if isinstance(build, str):
                    context, dockerfile = build, "Dockerfile"
                else:
                    context, dockerfile = build.get("context", "."), build.get("dockerfile", "Dockerfile")
                ctx_path = (base / context)
                df_path = (ctx_path / dockerfile)
                df_rel = str(PurePosixPath(*[p for p in df_path.parts if p != "."])) if df_path.parts else "Dockerfile"
                df_rel = df_rel.replace("//", "/")
                iid = address.built(df_rel)  # the image, by its Dockerfile
                if not m.has(iid):
                    m.add_entity(Entity(iid, "image", df_rel, {"dockerfile": df_rel, "context": str(ctx_path) if str(ctx_path) != "." else "."}, Provenance("compose", rel)))
                    m.add_relation(Relation(CONTAIN, m.ensure_root(address.PKG, "compose"), iid, provenance=Provenance("compose", rel)))
                m.add_relation(Relation(ALLOCATE, iid, sid, "runs_as", provenance=Provenance("compose", rel)))
            deps = spec.get("depends_on") or []
            for d in (deps if isinstance(deps, list) else list(deps.keys())):
                if d in svc_ids:
                    m.add_relation(Relation(USE, sid, svc_ids[d], "depends_on", provenance=Provenance("compose", rel)))
            env = spec.get("environment") or {}
            if isinstance(env, list):
                env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
            for k, v in env.items():
                eid = ensure_env(ctx, str(k))
                m.add_relation(Relation(PROVIDE, sid, eid, "env", provenance=Provenance("compose", rel), attrs={"value": str(v)[:200]}))
                if PERMISSION_KEY.search(str(k)):
                    continue  # allowed origins name who may call us, not whom we call
                for host in URL_HOST.findall(str(v)):
                    if host in svc_ids:
                        if host != name:
                            m.add_relation(Relation(USE, sid, svc_ids[host], "url", provenance=Provenance("compose", rel), attrs={"env": str(k)}))
                    elif host not in ("localhost", "127.0.0.1", "0.0.0.0") and "." in host:
                        hid = ensure_host(m, host, "compose", rel)
                        m.add_relation(Relation(USE, sid, hid, "url", provenance=Provenance("compose", rel), attrs={"env": str(k)}))
        ctx.notes.setdefault("compose-files", []).append(rel)
