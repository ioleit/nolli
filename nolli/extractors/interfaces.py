"""Interface families shared by adapters: how an endpoint, an env variable or a host is
named, so that a provider in one language and a user in another meet on the same entity.
Only the env family needs a helper here; APIs (endpoints) have their own shared
structure in ``api.py``."""

from __future__ import annotations

from ..model import CONTAIN, Entity, Provenance, Relation
from . import Context


def ensure_env(ctx: Context, name: str, extractor: str = "env") -> str:
    m = ctx.model
    if not m.has("env"):
        m.add_entity(Entity("env", "group", "Environment", {"synthetic": True}, Provenance(extractor)))
    eid = f"env:{name}"
    if not m.has(eid):
        m.add_entity(Entity(eid, "env", name, {}, Provenance(extractor)))
        m.add_relation(Relation(CONTAIN, "env", eid, provenance=Provenance(extractor)))
    return eid
