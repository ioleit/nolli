"""Binds the placeholders extractors leave to their real targets."""

from __future__ import annotations

from nolli.logic import extractors
from nolli.logic.extractors import Context, api
from nolli.logic.extractors.facts import Provenance, RelationFact
from nolli.model.schema import RelationKind, Scheme


def _prune_unresolved(ctx: Context) -> None:
    """Drop placeholders nothing refers to; park the rest under one group so they are
    counted as residue, never drawn as top-level nodes."""
    m = ctx.model
    referenced = {r.dst for r in m.relations} | {r.src for r in m.relations}
    for eid in [e for e, ent in m.entities.items() if ent.kind == "unresolved" and e not in referenced]:
        del m.entities[eid]
    m.relations = [r for r in m.relations if not (r.kind == RelationKind.CONTAIN and (r.src not in m.entities or r.dst not in m.entities))]
    m._parent = m._children = None
    loose = [e for e, ent in m.entities.items() if ent.kind == "unresolved" and m.parent(e) is None]
    if loose:
        group = m.ensure_root(Scheme.UNRESOLVED, "match")
        for e in loose:
            m.add_relation(RelationFact(RelationKind.CONTAIN, group, e, provenance=Provenance("match")))


def run_all(ctx: Context) -> None:
    """Runs each language's resolvers first (``Language.resolvers``: imports to files or
    external packages, references to symbols), then binds API calls to endpoints
    (heuristic, per protocol; ``api.resolve``). Placeholders that cannot be bound stay
    in the model as ``unresolved`` entities; placeholders bound everywhere are removed."""
    for _, fn in extractors.all_resolvers():
        fn(ctx)
    api.resolve(ctx)
    _prune_unresolved(ctx)
