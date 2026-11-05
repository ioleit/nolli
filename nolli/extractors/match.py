"""Resolution: bind placeholders produced by extractors to real targets.

- Each language's resolvers first (``Language.resolvers``: imports to files or external
  packages, references to symbols; see extractors/<language>/resolve.py).
- API calls -> endpoints (heuristic; per protocol, see ``extractors/api.py``).

Placeholders that cannot be bound stay in the model as ``unresolved``
entities so views can count them. Placeholders bound everywhere are removed.
"""

from __future__ import annotations

from nolli import extractors
from nolli.extractors import Context, api
from nolli.extractors.facts import Provenance, Relation
from nolli.model import metamodel


def _prune_unresolved(ctx: Context) -> None:
    """Drop placeholders nothing refers to; park the rest under one group so they are
    counted as residue, never drawn as top-level nodes."""
    m = ctx.model
    referenced = {r.dst for r in m.relations} | {r.src for r in m.relations}
    for eid in [e for e, ent in m.entities.items() if ent.kind == "unresolved" and e not in referenced]:
        del m.entities[eid]
    m.relations = [r for r in m.relations if not (r.kind == metamodel.CONTAIN and (r.src not in m.entities or r.dst not in m.entities))]
    m._parent = m._children = None
    loose = [e for e, ent in m.entities.items() if ent.kind == "unresolved" and m.parent(e) is None]
    if loose:
        group = m.ensure_root(metamodel.UNRESOLVED, "match")
        for e in loose:
            m.add_relation(Relation(metamodel.CONTAIN, group, e, provenance=Provenance("match")))


def run_all(ctx: Context) -> None:
    for _, fn in extractors.all_resolvers():
        fn(ctx)
    api.resolve(ctx)
    _prune_unresolved(ctx)
