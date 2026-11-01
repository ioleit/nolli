"""Interface families shared by adapters: how an env variable or an external package is
named, so that a provider in one language and a user in another meet on the same entity
(an npm package imported in TypeScript and loaded from a CDN by a page is one entity).
APIs (endpoints) have their own shared structure in ``api.py``."""

from __future__ import annotations

import re

from ..model import CONTAIN, Entity, Provenance, Relation, address
from . import Context


def ensure_env(ctx: Context, name: str, extractor: str = "env") -> str:
    """The setting an environment variable is (``config:NAME``)."""
    m = ctx.model
    eid = address.config(name)
    if not m.has(eid):
        m.add_entity(Entity(eid, "env", name, {}, Provenance(extractor)))
        m.add_relation(Relation(CONTAIN, m.ensure_root(address.CONFIG, extractor), eid, provenance=Provenance(extractor)))
    return eid


def ensure_ext(m, lang: str, name: str, extractor: str = "match") -> str:
    """A third-party package as ``lang``'s imports name it (``pkg:npm/elkjs``)."""
    eid = address.package(lang, name)
    if not m.has(eid):
        m.add_entity(Entity(eid, "package", name, {"language": lang}, Provenance(extractor)))
        m.add_relation(Relation(CONTAIN, m.ensure_root(address.PKG, extractor), eid, provenance=Provenance(extractor)))
    return eid


def ensure_host(m, host: str, extractor: str) -> str:
    """A host outside, reached by URL (``runtime:fonts.googleapis.com``)."""
    eid = address.runtime(host)
    if not m.has(eid):
        m.add_entity(Entity(eid, "host", host, {}, Provenance(extractor)))
        m.add_relation(Relation(CONTAIN, m.ensure_root(address.RUNTIME, extractor), eid, provenance=Provenance(extractor)))
    return eid


CDN = re.compile(r"^https?://(?:cdn\.jsdelivr\.net/npm/|unpkg\.com/|esm\.sh/(?:v\d+/)?|cdn\.skypack\.dev/|esm\.run/)"
                 r"((?:@[^/@]+/)?[^/@?]+)")


def is_url(spec: str) -> bool:
    """A URL that names a host, not a package on a package CDN."""
    return spec.startswith(("http://", "https://")) and not CDN.match(spec)


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
