"""Computes the ``hover`` overlay for a model, which describes each element in prose.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property

from nolli.logic.formats.descriptions import label, prose
from nolli.logic.formats.descriptions import names as names_lib
from nolli.model import schema, vocabulary
from nolli.model.model import Key, Relation
from nolli.model.scene import Element, Overlay, Scene
from nolli.model.schema import EntityId, RelationKind


class Source:
    """What the hover text reads off the scene: the structure from the
    model, what a file is from the file tree, what a module or class defines from its language,
    what an API is from its extractor, the references behind an edge."""

    def __init__(self, scene: Scene) -> None:
        self.scene, self.model = scene, scene.model
        self._attrs: dict[str, dict] = {}

    def has(self, x: str) -> bool:
        return x in self.model.entities

    def kind(self, x: str) -> str:
        return self.model.entities[x].kind

    def children(self, x: str) -> list[str]:
        return self.model.list_children(x)

    def parent(self, x: str) -> str | None:
        return self.model.find_parent(x)

    def element(self, x: str) -> Element:
        return self.scene.find_element(x)

    @cached_property
    def files(self) -> Mapping[str, dict]:
        return vocabulary.read_all_files(self.scene)

    @cached_property
    def symbols(self) -> Mapping[str, dict]:
        return vocabulary.read_all_symbols(self.scene)

    @cached_property
    def names(self) -> Mapping[str, dict]:
        return self.scene.read_all("names")

    def attrs(self, x: str) -> dict:
        a = self._attrs.get(x)
        if a is None:
            a = self._attrs[x] = {**self.files.get(x, {}), **self.symbols.get(x, {}), **names_lib.read_api(self.element(x))}
        return a

    def name(self, x: str) -> str:
        return self.names[x]["name"]

    def is_module(self, x: str) -> bool:
        return self.kind(x) == "file" and bool(self.attrs(x).get("module"))

    def is_init(self, x: str) -> bool:
        """Tell whether ``x`` is a package body, the file with a folder's own code
        (``__init__.py``, ``index.ts``)."""
        return self.has(x) and self.kind(x) == "file" and bool(self.attrs(x).get("body"))

    def chain_end(self, x: str, hidden: set[str]) -> str:
        """Follow ``x`` down a run of folders that each hold one folder, which is drawn as one box, and
        return the run's last folder. Hidden children and package bodies are not counted."""
        while self.kind(x) == "directory":
            kids = [c for c in self.children(x) if c not in hidden and not self.is_init(c)]
            if len(kids) == 1 and self.kind(kids[0]) == "directory":
                x = kids[0]
            else:
                break
        return x

    def list_relations(self):
        references = vocabulary.read_all_references(self.scene)
        for e in self.model.list_edges():
            yield e, sum(r["count"] for r in references.get(e, ()))

@dataclass
class NodeView:
    """A node of the source scene as the sentences read it."""
    id: str
    kind: str
    name: str
    attrs: dict


@dataclass
class Ctx:
    """Context that the hover description uses."""
    g: Source
    scene: Scene
    expanded: set[str]
    hidden: set[str]
    utilities: set[str] = field(default_factory=set)
    languages: dict = field(default_factory=dict)

    def node(self, x: str) -> NodeView:
        return NodeView(x, self.g.kind(x), self.g.name(x), self.g.attrs(x))

    @cached_property
    def labels(self) -> Mapping[Key, dict]:
        """The ``label`` overlay of every box in the scene."""
        return self.scene.read_all("label")

    def list_utilities_used(self, x: str) -> list[str]:
        if not self.scene.has(x):
            return []
        return (self.scene.find_element(x).get("projection") or {}).get("utilities_used") or []

    def name(self, x: str) -> str:
        """Return the name of a box as hover text writes it. A directory's name ends in "/", and a
        module's own code is "<module> (own code)". ``x`` may be a plain string, since overlay values such
        as ``utilities_used`` hold addresses as strings."""
        x = EntityId(x)
        if x.is_own_code:
            return f"{self.name(x.owner)} (own code)"
        e = self.node(x)
        if e.kind in ("service", "image"):
            return e.name.removesuffix("/Dockerfile")  # no "svc " / "img " prefix in prose
        if e.kind == "endpoint":
            return f"{e.attrs.get('method', '')} {e.attrs.get('path', e.name)}".strip()
        text = self.labels[x]["prose"] if x in self.labels else label.format_prose(self.g.element(x), self.g.names[x])
        return text + "/" if e.kind == "directory" and not text.endswith("/") else text

    def shown_children(self, x: str) -> list[str]:
        """List what unfolding x draws: the children that are not hidden, with a package's __init__
        replaced by its classes and single-child folder chains collapsed (rollup.visible_from_expanded)."""
        g, out = self.g, []
        kind = g.kind(x)
        for c in g.children(x):
            if c in self.hidden:
                continue
            if kind == "directory" and g.is_init(c):
                out.extend(cc for cc in g.children(c) if cc not in self.hidden)
                continue
            out.append(g.chain_end(c, self.hidden) if kind == "directory" and g.kind(c) == "directory" else c)
        return out

    def files_below(self, x: str) -> list[str]:
        """Files under x that the view shows (not hidden by their role)."""
        g, out, stack = self.g, [], [x]
        while stack:
            c = stack.pop()
            for k in g.children(c):
                if k in self.hidden:
                    continue
                if g.kind(k) == "file":
                    out.append(k)
                elif g.kind(k) == "directory":
                    stack.append(k)
        return out


def _by_use(ctx: Ctx, file_id: str, names: list[str]) -> list[str]:
    """Names defined in a module, most referenced from elsewhere first (references carry
    the name they use), then in source order."""
    uses: dict[str, int] = {}
    wanted = set(names)
    for e in ctx.g.model.list_in(file_id):
        if e.subkind != "ref":
            continue
        for f in vocabulary.read_references(ctx.g.element(e)):
            n = f.get("name")
            if n in wanted:
                uses[n] = uses.get(n, 0) + f["count"]
    order = {n: i for i, n in enumerate(names)}
    return sorted(names, key=lambda n: (-uses.get(n, 0), order[n]))



SYMBOL_NOUN = {"class": "class", "interface": "interface", "type": "type alias", "enum": "enum", "function": "function"}
GENERIC_NOUN = {"directory": "subfolder", "file": "file", **SYMBOL_NOUN, "endpoint": "endpoint", "group": "group",
                "env": "variable", "service": "service", "image": "image", "package": "package", "host": "host"}
WHAT = {"endpoint": "an HTTP endpoint", "env": "an environment variable", "service": "a Compose service",
        "image": "a Docker image", "package": "an external package", "host": "a host"}
NOUN_ORDER = ["subpackage", "folder module", "subfolder", "module", "class", "interface", "type alias", "enum"]  # earlier in a list; the rest by count


def _language(ctx: Ctx, x: str) -> str | None:
    """Return the name of an entity's language as text writes it ("TypeScript"), or None if it was
    not extracted."""
    return (ctx.languages or {}).get(ctx.g.attrs(x).get("language") or "")


def _body_of(ctx: Ctx, x: str) -> str | None:
    """Return a folder's package body (``__init__.py``, ``index.ts``), or None if ``x`` is not a
    folder or has none."""
    m = ctx.g
    return next((c for c in m.children(x) if m.is_init(c)), None) if m.kind(x) == "directory" else None


def _is_symbol(ctx: Ctx, x: str) -> bool:
    return ctx.g.kind(x) in schema.SYMBOL_KINDS and _language(ctx, x) is not None


def _noun(ctx: Ctx, x: str) -> str:
    """Return what a box counts as inside its folder, such as "subpackage", "module" or "class"."""
    e = ctx.node(x)
    body = _body_of(ctx, x)
    if body:
        what = ctx.g.attrs(body)["body"]
        return "sub" + what if what == "package" else what
    if ctx.g.is_module(x) and _language(ctx, x):
        return "module" if e.attrs.get("role") in ("source", None) else _language(ctx, x) + " file"
    return GENERIC_NOUN.get(e.kind, e.kind)


def _kind(ctx: Ctx, x: str) -> str | None:
    """Say what a folder is, such as "a Python package", for the sentence "<name> is <kind> with …".
    Return None if it has no package body in an extracted language."""
    body = _body_of(ctx, x)
    return f"a {_language(ctx, body)} {ctx.g.attrs(body)['body']}" if body and _language(ctx, body) else None


def _describe(ctx: Ctx, x: str) -> list[str] | None:
    """Write the first sentence for a module or a class or type; return None for anything else."""
    e = ctx.node(x)
    lang = _language(ctx, x)
    if ctx.g.is_module(x) and lang:
        facts = [prose.count(e.attrs["lines"], "line")] if e.attrs.get("lines") is not None else []
        inner: dict[str, int] = defaultdict(int)
        for c in ctx.g.children(x):
            inner[ctx.g.kind(c)] += 1
        facts += [prose.count(inner[k], SYMBOL_NOUN[k]) for k in SYMBOL_NOUN if inner.get(k)]
        if not facts:
            return [prose.say("{} is a " + lang + " module", ctx.name(x))]
        return [prose.say("{} is a " + lang + " module with ", ctx.name(x)) + prose.listing("{}", facts)]
    if _is_symbol(ctx, x):
        what = ("an exported " if e.attrs.get("exported") else "a ") + lang + " " + SYMBOL_NOUN[e.kind]
        where = (e.attrs.get("path") or "").rsplit("/", 1)[-1]
        if where and e.attrs.get("line"):
            return [prose.say("{} is " + what + " in {}, line {}", ctx.name(x), where, e.attrs["line"])]
        return [prose.say("{} is " + what, ctx.name(x))]
    return None


def _doc(ctx: Ctx, x: str) -> str | None:
    """Return the summary of a box's doc comment, taking a package's from its package body."""
    body = _body_of(ctx, x)
    if body or ctx.g.is_module(x) or _is_symbol(ctx, x):
        return ctx.node(body or x).attrs.get("doc")
    return None


def _own_code(ctx: Ctx, owner: str) -> list[str] | None:
    """Describe the own-code dot of a module (what it holds outside its classes and types) or of a
    package (what its package body defines and re-exports), one sentence per line. Return None
    for anything else."""
    body = _body_of(ctx, owner)
    if not body and not ctx.g.is_module(owner):
        return None
    f = body or owner
    a = ctx.node(f).attrs
    name = ctx.name(owner)
    lead = prose.say("{}'s {} defines ", name, ctx.node(f).name) if body else prose.say("{} defines ", name)
    functions = _by_use(ctx, f, a.get("functions") or [])
    constants = a.get("constants") or []
    lines: list[str] = []
    if functions:
        if len(functions) <= 3:  # "rollup defines bundle and rollup"
            lines.append(lead + prose.listing("{}", functions))
        else:  # "… defines 20 functions: is_init, … and 17 more" (most used first)
            lines.append(lead + prose.say("{}: ", prose.count(len(functions), "function")) + prose.listing("{}", functions, "more", limit=3))
    if constants:
        many = len(constants) > 3
        consts = (prose.say("{}: ", prose.count(len(constants), "constant")) if many else prose.say("the constant" + ("s " if len(constants) > 1 else " "))) \
            + prose.listing("{}", constants, "more", limit=3)
        lines.append(("Also " if functions else lead) + consts)
    if body and a.get("reexports"):
        lines.append(prose.say("It re-exports ") + prose.listing("{}", a["reexports"], "more", limit=4) + prose.say(" from its modules"))
    inside = "classes" if all(ctx.g.kind(c) == "class" for c in ctx.g.children(f)) else "classes and types"
    if not lines:
        lines.append(prose.say("{}'s {} only marks the folder as a " + a["body"], name, ctx.node(f).name) if body
                     else prose.say("{} has no functions or constants outside its " + inside, name))
    total, own = a.get("lines"), a.get("own_lines")
    if total and own is not None and not body and own != total:
        lines.append(prose.say("{} of its {} are outside its " + inside, f"{own:,}", prose.count(total, "line")))
    elif total:
        lines.append(prose.say("{}", prose.count(total, "line")))
    return lines


def _folder(ctx: Ctx, x: str) -> str:
    """"agents/ is a Python package with 6 subpackages and 3 modules": what it holds,
    counted as unfolding draws it (hidden roles left out, __init__ classes in)."""
    e = ctx.node(x)
    what = "the repository" if e.attrs.get("root") else _kind(ctx, x) or "a folder"
    by: dict[str, int] = defaultdict(int)
    for c in ctx.shown_children(x):
        by[_noun(ctx, c)] += 1
    if not by:
        return prose.say("{} is " + what, ctx.name(x))
    order = sorted(by, key=lambda k: (NOUN_ORDER.index(k) if k in NOUN_ORDER else len(NOUN_ORDER), -by[k], k))
    return prose.say("{} is " + what + " with ", ctx.name(x)) + prose.listing("{}", [prose.count(by[k], k) for k in order], limit=6)


def _size(ctx: Ctx, x: str) -> str | None:
    files = ctx.files_below(x)
    if not files:
        return None
    lines = sum(ctx.node(f).attrs.get("lines") or 0 for f in files)
    if lines:
        return prose.say("{} with {} in all", prose.count(len(files), "file"), prose.count(lines, "line"))
    return prose.say("{} in all", prose.count(len(files), "file"))


class Dependencies:
    """The external packages, environment variables, endpoints and deployment that the code
    in each drawn box uses. Taken from the facts rather than the badges, and built once for
    all boxes of a scene."""

    def __init__(self, ctx: Ctx, drawn: set[str]):
        m = ctx.g
        self.ext: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.env: dict[str, set[str]] = defaultdict(set)
        self.serves: dict[str, set[str]] = defaultdict(set)
        self.calls: dict[str, set[str]] = defaultdict(set)
        self.deployed: dict[str, set[str]] = defaultdict(set)

        def boxes(code: str):
            """Return the drawn boxes holding ``code`` (itself included), or none if it or a box
            around it is hidden."""
            chain, cur = [], code
            while cur is not None:
                if cur in ctx.hidden:
                    return []
                if cur in drawn:
                    chain.append(cur)
                cur = m.parent(cur)
            return chain

        for r, n in m.list_relations():
            if r.kind not in (RelationKind.USE, RelationKind.PROVIDE, RelationKind.ALLOCATE) or not m.has(r.dst) or not m.has(r.src):
                continue
            dk = m.kind(r.dst)
            if dk == "package" and r.kind == RelationKind.USE:
                for b in boxes(r.src):
                    self.ext[b][m.name(r.dst)] += n
            elif dk == "env" and r.kind == RelationKind.USE:
                for b in boxes(r.src):
                    self.env[b].add(m.name(r.dst))
            elif dk == "endpoint":
                target = self.serves if r.kind == RelationKind.PROVIDE else self.calls if r.kind == RelationKind.USE else None
                if target is not None:
                    for b in boxes(r.src):
                        target[b].add(r.dst)
            elif dk in ("service", "image") and r.kind == RelationKind.ALLOCATE:
                for b in boxes(r.src):
                    self.deployed[b].add(m.name(r.dst).removesuffix("/Dockerfile"))
            elif m.kind(r.src) in ("service", "image") and r.subkind == "entrypoint":
                for b in boxes(r.dst):
                    self.deployed[b].add(m.name(r.src).removesuffix("/Dockerfile"))

    def lines(self, ctx: Ctx, x: str) -> list[str]:
        out = []
        ext = self.ext.get(x)
        if ext:
            names = sorted(ext, key=lambda k: (-ext[k], k))
            out.append(prose.say("Uses {}: ", prose.count(len(names), "external package")) + prose.listing("{}", names, "more"))
        utils = ctx.list_utilities_used(x)
        if utils:
            out.append(prose.listing("Uses the utilit" + ("y" if len(utils) == 1 else "ies") + " {}", [ctx.name(u) for u in utils]))
        if self.env.get(x):
            out.append(prose.say("Reads {}", prose.count(len(self.env[x]), "environment variable")))
        if self.serves.get(x):
            out.append(prose.say("Serves {}", prose.count(len(self.serves[x]), "HTTP endpoint")))
        if self.calls.get(x):
            out.append(prose.say("Calls {}", prose.count(len(self.calls[x]), "HTTP endpoint")))
        if self.deployed.get(x):
            out.append(prose.listing("Deployed in {}", sorted(self.deployed[x])))
        return out


def _node_info(ctx: Ctx, deps: Dependencies, x: str, used_by: int = 0) -> str:
    """Write the text of a box's hover card as Markdown."""
    return prose.paragraphs(_node_lines(ctx, deps, x, used_by))


def _node_lines(ctx: Ctx, deps: Dependencies, x: str, used_by: int = 0) -> list[str]:
    m = ctx.g
    if x.is_own_code:
        said = _own_code(ctx, x.owner)
        if said:
            return said
        owner = ctx.name(x.owner)
        if ctx.node(x.owner).kind == "directory":
            body = next((ctx.node(c).name for c in m.children(x.owner) if m.is_init(c)), "package")
            return [prose.say("The code in {}'s {} beyond re-exporting its modules", owner, body)]
        return [prose.say("The code in {} outside its classes and types", owner)]
    e = ctx.node(x)
    lines: list[str] = []
    said = None if e.kind == "directory" else _describe(ctx, x)
    if said:
        lines += said
    else:
        if e.kind == "directory":
            lines.append(_folder(ctx, x))
        elif e.kind == "file":
            lines.append(prose.say("{} is a file with {}", ctx.name(x), prose.count(e.attrs["lines"], "line"))
                         if e.attrs.get("lines") is not None else prose.say("{} is a file", ctx.name(x)))
        elif e.kind == "group":
            lines.append(prose.say("{} holds {}", ctx.name(x), prose.count(len(m.children(x)), "entry", "entries")))
        else:
            lines.append(prose.say("{} is " + WHAT.get(e.kind, "a " + e.kind), ctx.name(x)))
    docstring = _doc(ctx, x)
    if docstring:
        lines.append(prose.quote(docstring))
    if e.kind == "directory":
        total = _size(ctx, x)
        if total:
            lines.append(total)
    lines += deps.lines(ctx, x)
    if used_by:
        lines.append(prose.say("A utility, used by {}: their arrows into it are left out", prose.count(used_by, "box", "boxes")))
    return lines


# per edge subkind: a sentence with {} for source, count and target
COUNTED = {
    "import": ("{} references {} in {}", "name"),
    "http": ("{} makes {} to endpoints served by {}", "call"),
    "env": ("{} reads {} from {}", "variable"),
}
# deployment relations: a sentence with {} for source and target
PLAIN = {
    "depends_on": "{} depends on {}",
    "url": "{} has a URL pointing at {}",
    "copies": "{} is copied into {}",
    "entrypoint": "{} starts {}",
    "runs_as": "{} runs as {}",
}


def _edge_info(ctx: Ctx, e) -> str:
    """Write the text of an edge's hover card as Markdown."""
    lines = _edge_lines(ctx, e)
    types = getattr(e, "types", 0)
    if e.subkind == "import" and types and types == e.count:
        lines.append(prose.say("Only types: no dependency at run time"))
    elif e.subkind == "import" and types:
        lines.append(prose.say("{} of {} only for types", f"{types:,}", prose.count(e.count, "reference") + " are" if types > 1 else prose.count(e.count, "reference") + " is"))
    return prose.paragraphs(lines)


def _edge_lines(ctx: Ctx, e) -> list[str]:
    src, dst = ctx.name(e.src), ctx.name(e.dst)
    sub = e.subkind
    if e.kind == "provide":
        return [prose.say("{} is served by {}", src, dst)]
    names = getattr(e, "names", None) or {}
    if sub == "import":
        if not names:  # import statements whose names were not recorded (TypeScript, unresolved Python)
            return [prose.say("{} imports {}", src, dst)]
        if set(names) == {ctx.node(e.dst).name} if ctx.g.has(e.dst) else False:
            return [prose.say("{} uses {}", src, dst)]  # the target is the one name used
        # the names themselves, most used first: "api/ references Store and list_items in lib/"
        top = sorted(names, key=lambda k: (-names[k], k))
        return [prose.say("{} references ", src) + prose.listing("{}", top, "more", limit=3) + prose.say(" in {}", dst)]
    if sub in COUNTED:
        template, noun_ = COUNTED[sub]
        return [prose.say(template, src, prose.count(e.count, noun_), dst)]
    return [prose.say(PLAIN.get(sub, "{} uses {}"), src, dst)]


class EdgeFacts:
    """A drawn edge as edge_info reads it: its key and what the projection counted."""

    def __init__(self, key: Relation, p: dict) -> None:
        self.kind, self.src, self.dst, self.subkind = key.kind, key.src, key.dst, key.subkind
        self.count, self.names, self.types = p.get("count", 0), p.get("names") or {}, p.get("type_refs", 0)


def _make_ctx(scene: Scene) -> tuple[Ctx, Dependencies]:
    """Build the context and the dependencies of the drawn boxes, which every description of one
    projected scene shares."""
    src = scene.sources["source"]
    meta = scene.model.meta.get("projection", {})
    ctx = Ctx(Source(src), scene, set(meta.get("expanded", [])), set(meta.get("hidden_nodes", [])),
              set(meta.get("utilities", [])), src.model.meta.get("languages") or {})
    return ctx, Dependencies(ctx, set(scene.model.entities))


class Hover(Overlay):
    """A Markdown description of each drawn box and edge, as ``{"text": ...}``.

    Each sentence is its own paragraph, the data in it is in code spans ("`api-mock`
    references `1 name` in `lib/`"), and a docstring is a quoted paragraph (see ``prose``).

    A box's description is built from the facts extraction wrote into the model and reads the
    same for every language. It says what the box is, such as a module (``module``) in some
    language (``meta["languages"]``), a folder that a package body (``body``) makes a package,
    the classes and types in a module, doc comments (``doc``) and a module's own code
    (``functions``, ``constants``, ``reexports``, ``own_lines``). It then says what unfolding a
    folder would show, how big the box is, and what it depends on that is not drawn as an edge,
    such as external packages, utilities, environment, deployment and HTTP. An edge's
    description lists what it carries, the names it references or calls, variables, or a
    deployment relation."""

    name = "hover"

    def compute(self, el: Element) -> dict | None:
        return el.scene.read_all(self.name).get(el.key)  # computed for the whole scene in compute_all

    def compute_all(self, scene: Scene) -> Mapping[Key, dict]:
        """Descriptions of all boxes and edges, built from the scene this one was drawn from.
        A scene not drawn from another has none."""
        if "source" not in scene.sources:
            return {}
        ctx, deps = _make_ctx(scene)
        known = lambda x: ctx.g.has(x.owner)  # the source lacks nodes that were added, such as a proposed directory
        out: dict[Key, dict] = {}
        for x in scene.model.entities:
            if not known(x):
                continue
            p = scene.find_element(x).get("projection") or {}
            out[x] = {"text": _node_info(ctx, deps, x, p.get("used_by", 0))}
        for e in scene.model.list_edges():
            if not (known(e.src) and known(e.dst)):
                continue
            p = scene.find_element(e).get("projection") or {}
            out[e] = {"text": _edge_info(ctx, EdgeFacts(e, p))}
        return out
