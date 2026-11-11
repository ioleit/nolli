"""Entities that providers and users in any language meet on.

Environment variables, external packages and outside hosts are named the same way
whichever language provides or uses them (an npm package imported in TypeScript and
loaded from a CDN by a page is one entity). APIs have their own structure in ``api``.
"""

from __future__ import annotations

import re

from nolli.logic.extractors import Context
from nolli.logic.extractors.facts import EntityFact, Provenance, RelationFact
from nolli.model.schema import EntityId, RelationKind, Scheme


def ensure_env(ctx: Context, name: str, extractor: str = "env") -> str:
    """Return the setting for an environment variable (``config:NAME``), adding it if missing."""
    m = ctx.model
    eid = EntityId.from_config(name)
    if not m.has(eid):
        m.add_entity(EntityFact(eid, "env", name, {}, Provenance(extractor)))
        m.add_relation(RelationFact(RelationKind.CONTAIN, m.ensure_root(Scheme.CONFIG, extractor), eid, provenance=Provenance(extractor)))
    return eid


def ensure_ext(m, lang: str, name: str, extractor: str = "match") -> str:
    """Return a third-party package as ``lang``'s imports name it (``pkg:npm/elkjs``), adding it if
    missing."""
    eid = EntityId.from_package(lang, name)
    if not m.has(eid):
        m.add_entity(EntityFact(eid, "package", name, {"language": lang}, Provenance(extractor)))
        m.add_relation(RelationFact(RelationKind.CONTAIN, m.ensure_root(Scheme.PKG, extractor), eid, provenance=Provenance(extractor)))
    return eid


def ensure_host(m, host: str, extractor: str, path: str | None = None) -> str:
    """Return an outside host reached by URL (``runtime:fonts.googleapis.com``), adding it if
    missing; ``path`` is the file that names it."""
    eid = EntityId.from_host(host)
    if not m.has(eid):
        m.add_entity(EntityFact(eid, "host", host, {}, Provenance(extractor, path)))
        m.add_relation(RelationFact(RelationKind.CONTAIN, m.ensure_root(Scheme.RUNTIME, extractor), eid, provenance=Provenance(extractor, path)))
    return eid


CDN = re.compile(r"^https?://(?:cdn\.jsdelivr\.net/npm/|unpkg\.com/|esm\.sh/(?:v\d+/)?|cdn\.skypack\.dev/|esm\.run/)"
                 r"((?:@[^/@]+/)?[^/@?]+)")


def is_url(spec: str) -> bool:
    """Tell whether ``spec`` is a URL that names a host, not a package on a package CDN."""
    return spec.startswith(("http://", "https://")) and not CDN.match(spec)


def package_name(spec: str) -> str:
    """Return the npm package a specifier names: ``@scope/name`` or ``name``, also inside a CDN
    URL (``https://cdn.jsdelivr.net/npm/mermaid@11/...``). For another URL, return its host."""
    cdn = CDN.match(spec)
    if cdn:
        return cdn.group(1)
    if spec.startswith(("http://", "https://")):
        return spec.split("/")[2]
    bare = spec.removeprefix("node:")
    return "/".join(bare.split("/")[:2]) if bare.startswith("@") else bare.split("/")[0]
