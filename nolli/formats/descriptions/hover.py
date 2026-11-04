"""``hover``: what the viewer shows when the pointer rests on a box or an edge: plain sentences.

A description is Markdown (prose.py): one paragraph per sentence, code spans for the data
in it ("`api-mock` references `1 name` in `lib/`"), a quoted paragraph for what the code
says about itself (a docstring). The viewer sets both apart.

What a box is, is said from the facts extraction wrote into the model, the same way for
every language: a module (``module``) and its language's name (``meta["languages"]``),
a folder made a package by its package body (``body``), the classes and types inside a
module, doc comments (``doc``), and a module's own code (``functions``, ``constants``,
``reexports``, ``own_lines``). Then what unfolding a folder shows, its size, and what a
box depends on outside the picture (external packages, utilities, environment,
deployment, HTTP), which the boxes themselves no longer carry as badges.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ...model import ALLOCATE, PROVIDE, SYMBOL_KINDS, USE, EdgeKey, address
from ...model.scene import Element, Scene
from ...model.vocabulary import read_file, read_references, read_symbols
from .label import format_prose
from .names import format_name, read_api
from .prose import count, listing, paragraphs, quote, say


class Source:
    """What the hover text reads off the scene a picture was drawn from: the structure from the
    model, what a file is from the file tree, what a module or class defines from its language,
    what an API is from its extractor, the references behind an edge."""

    def __init__(self, scene: Scene) -> None:
        self.scene, self.model = scene, scene.model
        self._attrs: dict[str, dict] = {}

    def has(self, x: str) -> bool:
        return x in self.model.kinds

    def kind(self, x: str) -> str:
        return self.model.kinds[x]

    def children(self, x: str) -> list[str]:
        return self.model.list_children(x)

    def parent(self, x: str) -> str | None:
        return self.model.find_parent(x)

    def element(self, x: str) -> Element:
        return self.scene.find_element(x)

    def attrs(self, x: str) -> dict:
        a = self._attrs.get(x)
        if a is None:
            el = self.element(x)
            a = self._attrs[x] = {**read_file(el), **read_symbols(el), **read_api(el)}
        return a

    def name(self, x: str) -> str:
        return format_name(self.element(x))

    def is_module(self, x: str) -> bool:
        return self.kind(x) == "file" and bool(self.attrs(x).get("module"))

    def is_init(self, x: str) -> bool:
        """A package body: the folder's own code (``__init__.py``, ``index.ts``)."""
        return self.has(x) and self.kind(x) == "file" and bool(self.attrs(x).get("body"))

    def chain_end(self, x: str, hidden: set[str]) -> str:
        """A run of folders each holding one folder, drawn as one box: its last folder."""
        while self.kind(x) == "directory":
            kids = [c for c in self.children(x) if c not in hidden and not self.is_init(c)]
            if len(kids) == 1 and self.kind(kids[0]) == "directory":
                x = kids[0]
            else:
                break
        return x

    def list_relations(self):
        for e in self.model.list_edges():
            yield e, sum(r["count"] for r in read_references(self.element(e)))

@dataclass
class NodeView:
    """A node of the source scene as the sentences read it."""
    id: str
    kind: str
    name: str
    attrs: dict


@dataclass
class Ctx:
    """What a description may look at: the scene drawn from (``g``), the projected scene, its
    unfolded and hidden nodes and utilities, and how the picture names things."""
    g: Source
    scene: Scene
    expanded: set[str]
    hidden: set[str]
    utilities: set[str] = field(default_factory=set)
    languages: dict = field(default_factory=dict)

    def node(self, x: str) -> NodeView:
        return NodeView(x, self.g.kind(x), self.g.name(x), self.g.attrs(x))

    def list_utilities_used(self, x: str) -> list[str]:
        if not self.scene.has(x):
            return []
        return (self.scene.find_element(x).get("projection") or {}).get("utilities_used") or []

    def name(self, x: str) -> str:
        """A box as the picture names it: directories end in "/", a module's own code says so."""
        if address.is_own_code(x):
            return f"{self.name(address.owner(x))} (own code)"
        e = self.node(x)
        if e.kind in ("service", "image"):
            return e.name.removesuffix("/Dockerfile")  # no "svc " / "img " prefix in prose
        if e.kind == "endpoint":
            return f"{e.attrs.get('method', '')} {e.attrs.get('path', e.name)}".strip()
        drawn = self.scene.find_element(x) if self.scene.has(x) else None
        label = format_prose(self.g.element(x), drawn)
        return label + "/" if e.kind == "directory" and not label.endswith("/") else label

    def shown_children(self, x: str) -> list[str]:
        """What unfolding x draws: children not hidden, a package's __init__ replaced by its
        classes, single-child folder chains collapsed (rollup.visible_from_expanded)."""
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


def by_use(ctx: Ctx, file_id: str, names: list[str]) -> list[str]:
    """Names defined in a module, most referenced from elsewhere first (references carry
    the name they use), then in source order."""
    uses: dict[str, int] = {}
    wanted = set(names)
    for e in ctx.g.model.list_in(file_id):
        if e.subkind != "ref":
            continue
        for f in read_references(ctx.g.element(e)):
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


def language(ctx: Ctx, x: str) -> str | None:
    """The name in text of an entity's language ("TypeScript"), if it was extracted."""
    return (ctx.languages or {}).get(ctx.g.attrs(x).get("language") or "")


def body_of(ctx: Ctx, x: str) -> str | None:
    """A folder's package body (``__init__.py``, ``index.ts``)."""
    m = ctx.g
    return next((c for c in m.children(x) if m.is_init(c)), None) if m.kind(x) == "directory" else None


def is_symbol(ctx: Ctx, x: str) -> bool:
    return ctx.g.kind(x) in SYMBOL_KINDS and language(ctx, x) is not None


def noun(ctx: Ctx, x: str) -> str:
    """What a box counts as inside its folder."""
    e = ctx.node(x)
    body = body_of(ctx, x)
    if body:
        what = ctx.g.attrs(body)["body"]
        return "sub" + what if what == "package" else what
    if ctx.g.is_module(x) and language(ctx, x):
        return "module" if e.attrs.get("role") in ("source", None) else language(ctx, x) + " file"
    return GENERIC_NOUN.get(e.kind, e.kind)


def kind(ctx: Ctx, x: str) -> str | None:
    """What a folder is, for "<name> is <kind> with …": "a Python package"."""
    body = body_of(ctx, x)
    return f"a {language(ctx, body)} {ctx.g.attrs(body)['body']}" if body and language(ctx, body) else None


def describe(ctx: Ctx, x: str) -> list[str] | None:
    """The first sentence for a module or a class or type."""
    e = ctx.node(x)
    lang = language(ctx, x)
    if ctx.g.is_module(x) and lang:
        facts = [count(e.attrs["lines"], "line")] if e.attrs.get("lines") is not None else []
        inner: dict[str, int] = defaultdict(int)
        for c in ctx.g.children(x):
            inner[ctx.g.kind(c)] += 1
        facts += [count(inner[k], SYMBOL_NOUN[k]) for k in SYMBOL_NOUN if inner.get(k)]
        if not facts:
            return [say("{} is a " + lang + " module", ctx.name(x))]
        return [say("{} is a " + lang + " module with ", ctx.name(x)) + listing("{}", facts)]
    if is_symbol(ctx, x):
        what = ("an exported " if e.attrs.get("exported") else "a ") + lang + " " + SYMBOL_NOUN[e.kind]
        where = (e.attrs.get("path") or "").rsplit("/", 1)[-1]
        if where and e.attrs.get("line"):
            return [say("{} is " + what + " in {}, line {}", ctx.name(x), where, e.attrs["line"])]
        return [say("{} is " + what, ctx.name(x))]
    return None


def doc(ctx: Ctx, x: str) -> str | None:
    """The doc comment's summary: a package's from its package body."""
    body = body_of(ctx, x)
    if body or ctx.g.is_module(x) or is_symbol(ctx, x):
        return ctx.node(body or x).attrs.get("doc")
    return None


def own_code(ctx: Ctx, owner: str) -> list[str] | None:
    """The own-code dot of a module (what it holds outside its classes and types) or of a
    package (what its package body defines and re-exports)."""
    body = body_of(ctx, owner)
    if not body and not ctx.g.is_module(owner):
        return None
    f = body or owner
    a = ctx.node(f).attrs
    name = ctx.name(owner)
    lead = say("{}'s {} defines ", name, ctx.node(f).name) if body else say("{} defines ", name)
    functions = by_use(ctx, f, a.get("functions") or [])
    constants = a.get("constants") or []
    lines: list[str] = []
    if functions:
        if len(functions) <= 3:  # "rollup defines bundle and rollup"
            lines.append(lead + listing("{}", functions))
        else:  # "… defines 20 functions: is_init, … and 17 more" (most used first)
            lines.append(lead + say("{}: ", count(len(functions), "function")) + listing("{}", functions, "more", limit=3))
    if constants:
        many = len(constants) > 3
        consts = (say("{}: ", count(len(constants), "constant")) if many else say("the constant" + ("s " if len(constants) > 1 else " "))) \
            + listing("{}", constants, "more", limit=3)
        lines.append(("Also " if functions else lead) + consts)
    if body and a.get("reexports"):
        lines.append(say("It re-exports ") + listing("{}", a["reexports"], "more", limit=4) + say(" from its modules"))
    inside = "classes" if all(ctx.g.kind(c) == "class" for c in ctx.g.children(f)) else "classes and types"
    if not lines:
        lines.append(say("{}'s {} only marks the folder as a " + a["body"], name, ctx.node(f).name) if body
                     else say("{} has no functions or constants outside its " + inside, name))
    total, own = a.get("lines"), a.get("own_lines")
    if total and own is not None and not body and own != total:
        lines.append(say("{} of its {} are outside its " + inside, f"{own:,}", count(total, "line")))
    elif total:
        lines.append(say("{}", count(total, "line")))
    return lines


def folder(ctx: Ctx, x: str) -> str:
    """"agents/ is a Python package with 6 subpackages and 3 modules": what it holds,
    counted as unfolding draws it (hidden roles left out, __init__ classes in)."""
    e = ctx.node(x)
    what = "the repository" if e.attrs.get("root") else kind(ctx, x) or "a folder"
    by: dict[str, int] = defaultdict(int)
    for c in ctx.shown_children(x):
        by[noun(ctx, c)] += 1
    if not by:
        return say("{} is " + what, ctx.name(x))
    order = sorted(by, key=lambda k: (NOUN_ORDER.index(k) if k in NOUN_ORDER else len(NOUN_ORDER), -by[k], k))
    return say("{} is " + what + " with ", ctx.name(x)) + listing("{}", [count(by[k], k) for k in order], limit=6)


def size(ctx: Ctx, x: str) -> str | None:
    files = ctx.files_below(x)
    if not files:
        return None
    lines = sum(ctx.node(f).attrs.get("lines") or 0 for f in files)
    if lines:
        return say("{} with {} in all", count(len(files), "file"), count(lines, "line"))
    return say("{} in all", count(len(files), "file"))


class Dependencies:
    """What each drawn box uses outside the picture, from the facts (not the badges): the
    external packages, environment variables, endpoints and deployment its code touches,
    collected once per scene for every drawn box."""

    def __init__(self, ctx: Ctx, drawn: set[str]):
        m = ctx.g
        self.ext: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.env: dict[str, set[str]] = defaultdict(set)
        self.serves: dict[str, set[str]] = defaultdict(set)
        self.calls: dict[str, set[str]] = defaultdict(set)
        self.deployed: dict[str, set[str]] = defaultdict(set)

        def boxes(code: str):
            """The drawn boxes holding code (itself included), unless it is hidden."""
            chain, cur = [], code
            while cur is not None:
                if cur in ctx.hidden:
                    return []
                if cur in drawn:
                    chain.append(cur)
                cur = m.parent(cur)
            return chain

        for r, n in m.list_relations():
            if r.kind not in (USE, PROVIDE, ALLOCATE) or not m.has(r.dst) or not m.has(r.src):
                continue
            d, s = ctx.node(r.dst), ctx.node(r.src)
            if d.kind == "package" and r.kind == USE:
                for b in boxes(r.src):
                    self.ext[b][d.name] += n
            elif d.kind == "env" and r.kind == USE:
                for b in boxes(r.src):
                    self.env[b].add(d.name)
            elif d.kind == "endpoint":
                target = self.serves if r.kind == PROVIDE else self.calls if r.kind == USE else None
                if target is not None:
                    for b in boxes(r.src):
                        target[b].add(d.id)
            elif d.kind in ("service", "image") and r.kind == ALLOCATE:
                for b in boxes(r.src):
                    self.deployed[b].add(d.name.removesuffix("/Dockerfile"))
            elif s.kind in ("service", "image") and r.subkind == "entrypoint":
                for b in boxes(r.dst):
                    self.deployed[b].add(s.name.removesuffix("/Dockerfile"))

    def lines(self, ctx: Ctx, x: str) -> list[str]:
        out = []
        ext = self.ext.get(x)
        if ext:
            names = sorted(ext, key=lambda k: (-ext[k], k))
            out.append(say("Uses {}: ", count(len(names), "external package")) + listing("{}", names, "more"))
        utils = ctx.list_utilities_used(x)
        if utils:
            out.append(listing("Uses the utilit" + ("y" if len(utils) == 1 else "ies") + " {}", [ctx.name(u) for u in utils]))
        if self.env.get(x):
            out.append(say("Reads {}", count(len(self.env[x]), "environment variable")))
        if self.serves.get(x):
            out.append(say("Serves {}", count(len(self.serves[x]), "HTTP endpoint")))
        if self.calls.get(x):
            out.append(say("Calls {}", count(len(self.calls[x]), "HTTP endpoint")))
        if self.deployed.get(x):
            out.append(listing("Deployed in {}", sorted(self.deployed[x])))
        return out


def node_info(ctx: Ctx, deps: Dependencies, x: str, used_by: int = 0) -> str:
    """What a box's hover card says, as Markdown."""
    return paragraphs(node_lines(ctx, deps, x, used_by))


def node_lines(ctx: Ctx, deps: Dependencies, x: str, used_by: int = 0) -> list[str]:
    m = ctx.g
    if address.is_own_code(x):
        said = own_code(ctx, address.owner(x))
        if said:
            return said
        owner = ctx.name(address.owner(x))
        if ctx.node(address.owner(x)).kind == "directory":
            body = next((ctx.node(c).name for c in m.children(address.owner(x)) if m.is_init(c)), "package")
            return [say("The code in {}'s {} beyond re-exporting its modules", owner, body)]
        return [say("The code in {} outside its classes and types", owner)]
    e = ctx.node(x)
    lines: list[str] = []
    said = None if e.kind == "directory" else describe(ctx, x)
    if said:
        lines += said
    else:
        if e.kind == "directory":
            lines.append(folder(ctx, x))
        elif e.kind == "file":
            lines.append(say("{} is a file with {}", ctx.name(x), count(e.attrs["lines"], "line"))
                         if e.attrs.get("lines") is not None else say("{} is a file", ctx.name(x)))
        elif e.kind == "group":
            lines.append(say("{} holds {}", ctx.name(x), count(len(m.children(x)), "entry", "entries")))
        else:
            lines.append(say("{} is " + WHAT.get(e.kind, "a " + e.kind), ctx.name(x)))
    docstring = doc(ctx, x)
    if docstring:
        lines.append(quote(docstring))
    if e.kind == "directory":
        total = size(ctx, x)
        if total:
            lines.append(total)
    lines += deps.lines(ctx, x)
    if used_by:
        lines.append(say("A utility, used by {}: their arrows into it are left out", count(used_by, "box", "boxes")))
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


def edge_info(ctx: Ctx, e) -> str:
    """What an edge's hover card says, as Markdown."""
    lines = edge_lines(ctx, e)
    types = getattr(e, "types", 0)
    if e.subkind == "import" and types and types == e.count:
        lines.append(say("Only types: no dependency at run time"))
    elif e.subkind == "import" and types:
        lines.append(say("{} of {} only for types", f"{types:,}", count(e.count, "reference") + " are" if types > 1 else count(e.count, "reference") + " is"))
    return paragraphs(lines)


def edge_lines(ctx: Ctx, e) -> list[str]:
    src, dst = ctx.name(e.src), ctx.name(e.dst)
    sub = e.subkind
    if e.kind == "provide":
        return [say("{} is served by {}", src, dst)]
    names = getattr(e, "names", None) or {}
    if sub == "import":
        if not names:  # import statements whose names were not recorded (TypeScript, unresolved Python)
            return [say("{} imports {}", src, dst)]
        if set(names) == {ctx.node(e.dst).name} if ctx.g.has(e.dst) else False:
            return [say("{} uses {}", src, dst)]  # the target is the one name used
        # the names themselves, most used first: "api/ references Store and list_items in lib/"
        top = sorted(names, key=lambda k: (-names[k], k))
        return [say("{} references ", src) + listing("{}", top, "more", limit=3) + say(" in {}", dst)]
    if sub in COUNTED:
        template, noun_ = COUNTED[sub]
        return [say(template, src, count(e.count, noun_), dst)]
    return [say(PLAIN.get(sub, "{} uses {}"), src, dst)]


class EdgeFacts:
    """A drawn edge as edge_info reads it: its key and what the projection counted."""

    def __init__(self, key: EdgeKey, p: dict) -> None:
        self.kind, self.src, self.dst, self.subkind = key.kind, key.src, key.dst, key.subkind
        self.count, self.names, self.types = p.get("count", 0), p.get("names") or {}, p.get("type_refs", 0)


def make_ctx(scene: Scene) -> tuple[Ctx, Dependencies]:
    """What every description of one projected scene shares, made once (``scene.memo``)."""
    src = scene.sources["source"]
    meta = scene.model.meta.get("projection", {})
    ctx = Ctx(Source(src), scene, set(meta.get("expanded", [])), set(meta.get("hidden_nodes", [])),
              set(meta.get("utilities", [])), src.model.meta.get("languages") or {})
    return ctx, Dependencies(ctx, set(scene.model.kinds))


class Hover:
    name = "hover"

    def compute(self, el: Element) -> dict | None:
        if "source" not in el.scene.sources:
            return None
        ctx, deps = el.scene.memo(make_ctx)
        if el.is_edge:
            if el.kind == "contain":
                return None
            return {"text": edge_info(ctx, EdgeFacts(el.key, el.get("projection") or {}))}
        used_by = (el.get("projection") or {}).get("used_by", 0)
        return {"text": node_info(ctx, deps, el.key, used_by)}
