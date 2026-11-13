"""What a projection is asked for: a ``ProjectionSpec``, which containers it unfolds (an
``Expansion``), its text form (the viewer's URL parameters) and the key of the model it makes
(``make_key``). Nothing here projects: that is ``_core.fold``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode

from nolli.logic.transformations.projection import rollup
from nolli.logic.transformations.projection import unfold as unfold_lib
from nolli.model.keys import ModelKey, ProjectionKey
from nolli.model.schema import EntityId, Scheme

if TYPE_CHECKING:
    from nolli.logic.transformations.projection.reading import Reading

ALL_ROLES = ("source", "mixed", "test", "docs", "config", "markup", "data", "asset", "other")
DEFAULT_ROLES = tuple(r for r in ALL_ROLES if r not in rollup.HIDDEN_ROLES_DEFAULT)
DEFAULT_BUDGET = 34
DEFAULT_UTILITY_IN = 4  # more incoming edges than this: a utility, drawn as badges
DEFAULT_MAX_LINES = 4  # lines between two drawn boxes at most; more are lifted to a box they are inside (rollup.lift)
DEFAULT_GROUPS = (EntityId.root(Scheme.API),)  # root groups drawn as boxes; the others are badges on their users


class Expansion:
    """Which containers a projection unfolds. ``find`` answers for the scene read by ``g``, given
    what is hidden and the rest of the spec. ``name``: what the viewer calls it (its presets),
    None for an explicit list. ``to_query`` writes it as the viewer's URL parameters do."""

    name: str | None = None

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        raise NotImplementedError

    def to_query(self) -> dict[str, str]:
        """Return it as URL parameters: ``p=<name>`` for a preset that needs nothing more."""
        if self.name is None:
            raise NotImplementedError(f"{type(self).__name__} has no text form")
        return {"p": self.name}

    def name_for(self, g: Reading) -> str | None:
        """Return what the viewer calls what ``find`` did on ``g``; a rule may fall back to another."""
        return self.name


@dataclass(frozen=True)
class Ids(Expansion):
    """These containers (those the scene has)."""
    ids: tuple[str, ...]

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {x for x in self.ids if g.has(x)}

    def to_query(self) -> dict[str, str]:
        return {"x": ",".join(sorted(self.ids))}


@dataclass(frozen=True)
class Dirs(Expansion):
    """Every directory, the repository's included: module level, no classes. With ``under`` (a
    repository path), only the directories at or below it and those on the way to it."""
    under: str = ""
    name = "dirs"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        def wanted(x: str) -> bool:
            if x == EntityId.REPO_ROOT:
                return True
            if g.kind(x) != "directory":
                return False
            path = x.path
            return not self.under or path == self.under or path.startswith(self.under + "/") or self.under.startswith(path + "/")
        return {x for x in g.nodes if g.children(x) and wanted(x)}

    def to_query(self) -> dict[str, str]:
        return {"p": self.name, **({"d": self.under} if self.under else {})}


@dataclass(frozen=True)
class Root(Expansion):
    """The top level only."""
    name = "folded"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {EntityId.REPO_ROOT}


@dataclass(frozen=True)
class All(Expansion):
    """Every container that is not hidden."""
    name = "unfolded"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        return {x for x in g.nodes if x not in hidden and g.children(x)}


@dataclass(frozen=True)
class AtMost(Expansion):
    """The default cut: as much unfolded as about ``budget`` boxes allow (hidden roles do not count)."""
    budget: int = DEFAULT_BUDGET
    name = "default"

    def find(self, g: Reading, hidden: set[str], st: ProjectionSpec) -> set[str]:
        hidden_roles = [r for r in ALL_ROLES if r not in set(st.roles)]
        return set(unfold_lib.find_default_cut(g, budget=self.budget, hidden_roles=hidden_roles, hidden=hidden)["expanded"])

    def to_query(self) -> dict[str, str]:
        return {"p": self.name, **({"b": str(self.budget)} if self.budget != DEFAULT_BUDGET else {})}


PRESETS: dict[str, type[Expansion]] = {"default": AtMost, "folded": Root, "unfolded": All}  # the viewer's presets that need nothing but the scene
_NAMED: dict[str, type[Expansion]] = {**PRESETS, "dirs": Dirs}  # every named expansion this module has


@dataclass
class ProjectionSpec:
    """What a projection is asked for: which containers are unfolded (``expand``), the roles and
    namespace groups shown, how edges are simplified (``utilities``, ``max_lines``), and for a
    change which side. A container unfolded inside a folded one stays folded."""
    expand: Expansion = field(default_factory=AtMost)
    roles: list[str] = field(default_factory=lambda: list(DEFAULT_ROLES))  # shown roles
    groups: list[str] = field(default_factory=lambda: list(DEFAULT_GROUPS))  # shown root groups
    utilities: int = DEFAULT_UTILITY_IN  # boxes with more incoming edges are badges on their users; 0: off
    side: str = "both"  # for a change, "base" or "head" draws one commit and "both" the merged model
    max_lines: int = DEFAULT_MAX_LINES  # lines drawn between two boxes at most (rollup.lift)

    def to_query(self) -> dict[str, str]:
        """Return the spec as the viewer's URL parameters (its location hash) write it, leaving
        out what is as by default: ``x`` the unfolded containers or ``p`` a preset (with ``b`` a
        budget, ``d`` a path), ``r`` the roles, ``g`` the groups, ``ut`` / ``nu`` utilities,
        ``s`` the side, ``ml`` the lines between two boxes. Lists are sorted, so a spec has one
        text form (``format``)."""
        q = self.expand.to_query()
        if sorted(self.roles) != sorted(DEFAULT_ROLES):
            q["r"] = ",".join(sorted(self.roles))
        if sorted(self.groups) != sorted(DEFAULT_GROUPS):
            q["g"] = ",".join(sorted(self.groups))
        if self.utilities != DEFAULT_UTILITY_IN:
            q.update({"nu": "1"} if self.utilities == 0 else {"ut": str(self.utilities)})
        if self.side != "both":
            q["s"] = self.side
        if self.max_lines != DEFAULT_MAX_LINES:
            q["ml"] = str(self.max_lines)
        return q

    @classmethod
    def from_query(cls, q: Mapping[str, str], presets: Mapping[str, type[Expansion]] = PRESETS) -> ProjectionSpec:
        """Read a spec from URL parameters as ``to_query`` (and the viewer) writes them. ``p``
        names one of ``presets`` (a caller adds its own, as ``merge.presets.PRESETS`` does) or of
        this module's named expansions; an unknown one, like none, gives the default cut."""
        st = cls()
        named = {**_NAMED, **presets}
        if q.get("x"):
            st.expand = Ids(tuple(x for x in q["x"].split(",") if x))
        elif q.get("p") == Dirs.name:
            st.expand = Dirs(q.get("d", ""))
        elif q.get("p") in named:
            st.expand = named[q["p"]]()
        if q.get("b") and isinstance(st.expand, AtMost):  # the default cut's budget
            st.expand = AtMost(int(q["b"]))
        if q.get("r") is not None:
            st.roles = [r for r in q["r"].split(",") if r]
        if q.get("g") is not None:
            st.groups = [x for x in q["g"].split(",") if x]
        st.utilities = 0 if q.get("nu") == "1" else int(q["ut"]) if q.get("ut") else DEFAULT_UTILITY_IN
        if q.get("s") in ("base", "head"):
            st.side = q["s"]
        if q.get("ml"):
            st.max_lines = max(1, int(q["ml"]))
        return st

    def format(self) -> str:
        """Return the spec as text, its URL parameters (``to_query``) in one fixed order."""
        return urlencode(self.to_query(), safe=":/,#")

    @classmethod
    def parse(cls, text: str, presets: Mapping[str, type[Expansion]] = PRESETS) -> ProjectionSpec:
        """Read a spec back from ``format``'s text."""
        return cls.from_query(dict(parse_qsl(text, keep_blank_values=True)), presets)


def make_key(source: ModelKey, st: ProjectionSpec) -> ProjectionKey:
    """Return the key of the model ``st`` projects from the model of ``source``, without projecting:
    the same spec of the same model always draws the same."""
    return ProjectionKey(source, st.format())
