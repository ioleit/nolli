"""Interface families shared by adapters: how an env variable or an external package is
named, so that a provider in one language and a user in another meet on the same entity
(an npm package imported in TypeScript and loaded from a CDN by a page is one entity).
APIs (endpoints) have their own shared structure in ``api.py``."""

from __future__ import annotations

import re

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


def ensure_ext(m, lang: str, name: str, extractor: str = "match") -> str:
    """The entity of an external package (``ext:py:requests``) under the externals group."""
    if not m.has("ext"):
        m.add_entity(Entity("ext", "group", "External packages", {"synthetic": True}, Provenance(extractor)))
    eid = f"ext:{lang}:{name}"
    if not m.has(eid):
        m.add_entity(Entity(eid, "package", name, {"language": lang}, Provenance(extractor)))
        m.add_relation(Relation(CONTAIN, "ext", eid, provenance=Provenance(extractor)))
    return eid


CDN = re.compile(r"^https?://(?:cdn\.jsdelivr\.net/npm/|unpkg\.com/|esm\.sh/(?:v\d+/)?|cdn\.skypack\.dev/|esm\.run/)"
                 r"((?:@[^/@]+/)?[^/@?]+)")


def package_name(spec: str) -> str:
    """The npm package a specifier names: ``@scope/name`` or ``name``, also inside a CDN
    URL (``https://cdn.jsdelivr.net/npm/mermaid@11/...``); another URL is its host."""
    cdn = CDN.match(spec)
    if cdn:
        return cdn.group(1)
    if spec.startswith(("http://", "https://")):
        return spec.split("/")[2]
    bare = spec.removeprefix("node:")
    return "/".join(bare.split("/")[:2]) if bare.startswith("@") else bare.split("/")[0]
