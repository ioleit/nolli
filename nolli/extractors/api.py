"""APIs: what a component serves for others to call, whatever the language or framework.

Adapters only report what they find in their framework's idioms:

- ``provide(ctx, Operation(...))``: a served operation (``python/fastapi.py``, ...);
- ``call(ctx, Call(...))``: a call site (``typescript/fetch.py``, ...).

``build`` (run after every adapter) turns the reported operations into one structure,
the same for every protocol, and ``resolve`` (run by ``nolli.match``) binds calls to
operations with the protocol's matcher (``MATCHERS``). Only the matcher and the way a
path splits into segments are protocol knowledge; the rest is shared.

The structure (entity kind, id):

    group     api                                  "APIs": the root group the filter shows or hides
    api       api:<protocol>:<key>                 one served API: an app, a server, a service definition
    resource  api:<protocol>:<key>/<seg>/<seg>     a step in the API's path tree (a parameter is ``{}``)
    endpoint  api:<protocol>:<key> <VERB> <path>   one operation

Names follow the picture's pattern, icons then a readable name (``nolli/labels.py``):
an API is named after the directory it is declared in (``API server``), a resource by
its own segments relative to its parent, which is drawn whenever it is (``/ assistant``,
``/ api`` at the top),
an operation by its verb and the path below its step, usually nothing (``GET``). Path
steps without an operation of their own and with one step below are merged with it
(``conversations/{project_name}``); a step with one operation and nothing below is no
box: the operation takes its place (``GET setup/status``). The entity's ``name`` is the
whole text (``GET setup/status``), ``attrs["name"]`` the part after the verb.

Relations: ``provide(handler file, endpoint)`` and, after matching,
``use(caller file, endpoint)``. Unmatched calls stay ``unresolved:<protocol>:...``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..model import CONTAIN, PROVIDE, USE, Entity, Provenance, Relation
from . import Context

GROUP = "api"


@dataclass
class Operation:
    protocol: str  # "http"; later "grpc", "graphql", ...
    api: str  # the served API's key, unique within the protocol (e.g. "server/main.py:app")
    api_file: str  # where the API is declared: it is named after that place ("server")
    api_about: str  # what it is, for the hover text ("FastAPI app `app` in server/main.py")
    verb: str  # GET, POST, WEBSOCKET, ... or an RPC method name
    path: str  # "/api/projects/{name}"; for RPC "Service/Method"
    file: str  # the file that provides it (the handler's)
    line: int
    handler: str = ""
    exact: bool = True  # False when the adapter guessed (an HTTP router never mounted)
    extractor: str = ""


@dataclass
class Call:
    protocol: str
    verb: str  # "?" when unknown
    pattern: str  # the called path's static shape, "*" for unknown segments
    file: str
    line: int
    via: str = "direct"
    extractor: str = ""


def provide(ctx: Context, op: Operation) -> None:
    ctx.notes.setdefault("api-operations", []).append(op)


def call(ctx: Context, c: Call) -> None:
    m = ctx.model
    uid = f"unresolved:{c.protocol}:{c.verb} {c.pattern}"
    if not m.has(uid):
        m.add_entity(Entity(uid, "unresolved", f"{c.verb} {c.pattern}",
                            {"protocol": c.protocol, "method": c.verb, "pattern": c.pattern}, Provenance(c.extractor)))
    m.add_relation(Relation(USE, ctx.fs_id(c.file), uid, c.protocol, confidence="heuristic",
                            provenance=Provenance(c.extractor, c.file, c.line), attrs={"via": c.via}))


# ---------- the shared structure ----------

def segments(protocol: str, path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def step_key(seg: str) -> str:
    """Parameters (adapters write them ``{name}``) at one position are one step, whatever
    each route calls them: ``/projects/{name}`` and ``/projects/{project_name}``."""
    return "{}" if seg.startswith("{") and seg.endswith("}") else seg


@dataclass
class _Step:
    children: dict
    ops: list
    names: dict  # the segment as written -> how many operations below spell it so

    def label(self) -> str:
        return "|".join(sorted(self.names, key=lambda n: (-self.names[n], n)))


def _step() -> _Step:
    return _Step({}, [], defaultdict(int))


def api_names(files: dict[tuple[str, str], str]) -> dict[tuple[str, str], str]:
    """(protocol, key) -> a readable name: the directory the API is declared in (its
    module at the top level), the whole path without extension where that is taken."""
    def short(f: str) -> str:
        d, _, base = f.rpartition("/")
        return d or base.rsplit(".", 1)[0]

    out = {k: short(f) for k, f in files.items()}
    taken: dict[str, int] = defaultdict(int)
    for n in out.values():
        taken[n] += 1
    for k, f in files.items():
        if taken[out[k]] > 1:
            out[k] = f.rsplit(".", 1)[0] + ("" if k[1].endswith(":app") else ":" + k[1].rsplit(":", 1)[-1])
    return out


def build(ctx: Context) -> None:
    ops: list[Operation] = ctx.notes.pop("api-operations", [])
    if not ops:
        return
    m = ctx.model
    prov = Provenance("api")
    m.add_entity(Entity(GROUP, "group", "APIs", {"synthetic": True}, prov))

    by_api: dict[tuple[str, str], list[Operation]] = defaultdict(list)
    for op in ops:
        by_api[(op.protocol, op.api)].append(op)

    names = api_names({k: v[0].api_file for k, v in by_api.items()})
    for (protocol, key), api_ops in sorted(by_api.items()):
        aid = f"api:{protocol}:{key}"
        m.add_entity(Entity(aid, "api", names[(protocol, key)],
                            {"protocol": protocol, "key": key, "file": api_ops[0].api_file,
                             "about": api_ops[0].api_about, "operations": len(api_ops)}, prov))
        m.add_relation(Relation(CONTAIN, GROUP, aid, provenance=prov))

        root = _step()
        for op in api_ops:
            s = root
            for seg in segments(protocol, op.path):
                s = s.children.setdefault(step_key(seg), _step())
                s.names[seg] += 1
            s.ops.append(op)

        def endpoint(op: Operation, parent: str, name: str) -> None:
            eid = f"{aid} {op.verb} {op.path}"
            if not m.has(eid):
                m.add_entity(Entity(eid, "endpoint", f"{op.verb} {name}".rstrip(),
                                    {"protocol": protocol, "method": op.verb, "path": op.path, "api": aid, "name": name},
                                    Provenance(op.extractor, op.file, op.line)))
                m.add_relation(Relation(CONTAIN, parent, eid, provenance=prov))
            m.add_relation(Relation(PROVIDE, ctx.fs_id(op.file), eid, "route", confidence="exact" if op.exact else "heuristic",
                                    provenance=Provenance(op.extractor, op.file, op.line), attrs={"handler": op.handler}))

        def emit(s: _Step, parent: str, path: list[str], label: list[str]) -> None:
            # merge steps that add nothing: no operation of their own, one step below
            while not s.ops and len(s.children) == 1:
                seg, s = next(iter(s.children.items()))
                path, label = path + [seg], label + [s.label()]
            text = "/".join(label)  # relative to its parent; the "/" icon says it is a path
            verbs = sorted({op.verb for op in s.ops})
            if len(verbs) == 1 and not s.children:
                for op in s.ops:
                    endpoint(op, parent, text)
                return
            rid = f"{aid}/" + "/".join(path)
            m.add_entity(Entity(rid, "resource", text, {"protocol": protocol, "path": "/" + "/".join(path), "api": aid}, prov))
            m.add_relation(Relation(CONTAIN, parent, rid, provenance=prov))
            for op in sorted(s.ops, key=lambda o: o.verb):
                endpoint(op, rid, "")
            for seg, child in sorted(s.children.items()):
                emit(child, rid, path + [seg], [child.label()])

        for op in sorted(root.ops, key=lambda o: o.verb):
            endpoint(op, aid, "/")
        for seg, child in sorted(root.children.items()):
            emit(child, aid, [seg], [child.label()])


# ---------- matching calls to operations ----------

def http_path_matches(route: str, called: str) -> bool:
    rs, cs = route.strip("/").split("/"), called.strip("/").split("/")
    for i, rseg in enumerate(rs):
        if rseg.startswith("{") and rseg.endswith(":path}"):
            return i < len(cs)
        if i >= len(cs):
            return False
        cseg = cs[i]
        if rseg.startswith("{") or cseg == "*":
            continue
        if rseg != cseg:
            return False
    return len(cs) == len(rs)


def http_suffix_matches(route: str, called: str) -> bool:
    rs, cs = route.strip("/").split("/"), called.strip("/").split("/")
    if len(cs) > len(rs):
        return False
    return http_path_matches("/" + "/".join(rs[len(rs) - len(cs):]), called)


def match_http(endpoints: list[Entity], verb: str, pattern: str) -> tuple[list[Entity], str]:
    def verb_ok(e: Entity) -> bool:
        return verb == "?" or e.attrs["method"] in ("ANY", verb) or verb in e.attrs["method"].split(",")

    hits = [e for e in endpoints if verb_ok(e) and http_path_matches(e.attrs["path"], pattern)]
    if hits or pattern.count("/") < 2:
        return hits, "path"
    # unknown base prefix on the caller's side (``${API_BASE}`` we could not fold):
    # accept routes whose path ends with the called pattern
    return [e for e in endpoints if verb_ok(e) and http_suffix_matches(e.attrs["path"], pattern)], "suffix"


def match_exact(endpoints: list[Entity], verb: str, pattern: str) -> tuple[list[Entity], str]:
    """Protocols whose operations are named, not patterned (RPC): verb and path must equal."""
    return [e for e in endpoints if e.attrs["path"] == pattern and verb in ("?", e.attrs["method"])], "exact"


MATCHERS = {"http": match_http}


def resolve(ctx: Context) -> None:
    m = ctx.model
    endpoints: dict[str, list[Entity]] = defaultdict(list)
    for e in m.entities.values():
        if e.kind == "endpoint":
            endpoints[e.attrs.get("protocol", "http")].append(e)
    stats: dict[str, int] = defaultdict(int)
    out: list[Relation] = []
    for r in m.relations:
        ph = m.entities.get(r.dst) if r.kind == USE and r.dst.startswith("unresolved:") else None
        protocol = ph.attrs.get("protocol") if ph is not None else None
        if protocol is None:
            out.append(r)
            continue
        hits, how = MATCHERS.get(protocol, match_exact)(endpoints[protocol], ph.attrs["method"], ph.attrs["pattern"])
        if hits:
            stats[f"{protocol} matched-{how}"] += 1
            for e in hits:
                out.append(Relation(USE, r.src, e.id, protocol, 1, "heuristic", r.provenance,
                                    dict(r.attrs, pattern=ph.attrs["pattern"], match=how)))
        else:
            stats[f"{protocol} unmatched"] += 1
            out.append(r)
    m.relations = out
    ctx.notes["api-resolution"] = dict(stats)
