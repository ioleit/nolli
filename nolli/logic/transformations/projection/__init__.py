"""Applies a projection, which folds (collapses) some entities into aggregates according to a ``ProjectionSpec``.

The package holds what only a projection needs: what it is asked for, its text form and the
key of what it makes (``spec``), the projection itself (``_core``: ``fold``), what it reads off
a scene (``reading``), the roll-up of the dependency graph to the visible nodes (``rollup``)
and the choice of which containers to unfold for a node budget (``unfold``). The names others
use are re-exported here, so a caller says ``projection.fold`` and ``projection.ProjectionSpec``."""

from __future__ import annotations

from nolli.logic.transformations.projection._core import fold
from nolli.logic.transformations.projection.spec import (
    ALL_ROLES,
    DEFAULT_BUDGET,
    DEFAULT_GROUPS,
    DEFAULT_MAX_LINES,
    DEFAULT_ROLES,
    DEFAULT_UTILITY_IN,
    PRESETS,
    All,
    AtMost,
    Dirs,
    Expansion,
    Ids,
    ProjectionSpec,
    Root,
    make_key,
)

__all__ = ["ALL_ROLES", "DEFAULT_BUDGET", "DEFAULT_GROUPS", "DEFAULT_MAX_LINES", "DEFAULT_ROLES", "DEFAULT_UTILITY_IN",
           "PRESETS", "All", "AtMost", "Dirs", "Expansion", "Ids", "ProjectionSpec", "Root", "fold", "make_key"]
