"""Python: classes as entities inside modules, and the references between them.

Each top-level ``class`` becomes an entity ``repo:<file>#<Name>`` contained in
its module (a class is in exactly one file, so ``contain`` stays a tree).
Functions are not entities: a module's functions and its module-level code
are the module's own body, so their references are attributed to the module
itself. A module without classes therefore has nothing to unfold. This is
deliberate: the picture is about architecture, not call graphs.

References are ``use`` relations with subkind ``ref`` (attrs ``name``: the name used): from the class whose
body mentions a name (methods count for their class) or from the module, to
the class the name is bound to, or to the module that defines it when the
name is a function, a variable or a submodule. Names bound by imports are
recorded against ``unresolved:pyref:`` placeholders that ``nolli.match``
resolves; references into external modules are dropped (the import edge
already covers them). Where references resolve, they replace the file-level
import edge, so a directory-level count means "references", not "import
statements".
"""

from __future__ import annotations

import ast
from collections import Counter

from ...model import USE, Entity, Provenance, Relation, address
from .. import Context

FUNC_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
DOC_MAX = 240  # characters of a docstring's summary kept


NAMES_MAX = 60  # names kept per list (functions, constants, re-exports)


def own_code(tree: ast.Module, rel: str, pkg_parts: list[str], aliases: dict[str, tuple[str, str]], total_lines: int | None) -> dict:
    """What a module holds outside its classes, for the hover text on its own-code dot:
    top-level functions and constants (UPPER_CASE names) in source order, the lines not
    inside a class, and for a package's __init__.py the names it re-exports from its own
    package (relative imports, or absolute ones into the package's dotted path)."""
    out: dict = {}
    functions = [n.name for n in tree.body if isinstance(n, FUNC_DEFS)]
    constants = []
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id.isupper() and t.id not in constants:
                constants.append(t.id)
    if functions:
        out["functions"] = functions[:NAMES_MAX]
    if constants:
        out["constants"] = constants[:NAMES_MAX]
    if total_lines is not None:
        in_classes = sum((n.end_lineno or n.lineno) - n.lineno + 1 for n in tree.body if isinstance(n, ast.ClassDef))
        out["own_lines"] = max(0, total_lines - in_classes)
    if rel.endswith("__init__.py"):
        suffixes = {".".join(pkg_parts[i:]) for i in range(len(pkg_parts))}
        reexports = [name for name, (mod, _) in aliases.items()
                     if any(mod == s or mod.startswith(s + ".") for s in suffixes)]
        if reexports:
            out["reexports"] = reexports[:NAMES_MAX]
    return out


def doc_summary(node) -> str | None:
    """A module's or class's docstring, read from the syntax tree (nothing is imported or
    run): its first paragraph on one line, cut at DOC_MAX characters."""
    text = ast.get_docstring(node, clean=True)
    if not text:
        return None
    para = " ".join(text.strip().split("\n\n", 1)[0].split())
    return para if len(para) <= DOC_MAX else para[: DOC_MAX - 1].rsplit(" ", 1)[0] + "…"


# Module-level assignments that define a type rather than a value, drawn as symbols like
# classes (kinds from model.SYMBOL_KINDS). Explicit forms are taken as they are: a ``type``
# statement, ``X: TypeAlias = ...``, and the factories below. A plain ``X = A | B`` or
# ``X = dict[str, int]`` counts only when X is CapWords, so ``FLAGS = READ | WRITE`` stays
# a constant and ``rows = table["x"]`` a value.
TYPE_FACTORIES = {"NewType": "type", "TypeVar": "type", "ParamSpec": "type", "TypeVarTuple": "type",
                  "NamedTuple": "class", "TypedDict": "class",
                  "Enum": "enum", "IntEnum": "enum", "StrEnum": "enum", "Flag": "enum", "IntFlag": "enum"}
TYPE_GENERICS = {"Union", "Optional", "Callable", "Literal", "Annotated", "Type", "TypeGuard", "TypeIs", "ClassVar",
                 "Final", "Required", "NotRequired", "dict", "list", "tuple", "set", "frozenset", "type",
                 "Mapping", "MutableMapping", "Sequence", "MutableSequence", "Iterable", "Iterator", "Generator",
                 "Collection", "Container", "Awaitable", "Coroutine", "AsyncIterator", "AsyncIterable"}


def last_name(e: ast.expr) -> str | None:
    """``Name`` or the last part of an attribute chain, else None."""
    return e.id if isinstance(e, ast.Name) else e.attr if isinstance(e, ast.Attribute) else None


def is_type_expr(e: ast.expr) -> bool:
    """A union written with ``|`` or a subscripted generic (``dict[str, int]``, ``Optional[X]``)."""
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
        return is_type_operand(e.left) and is_type_operand(e.right)
    return isinstance(e, ast.Subscript) and last_name(e.value) in TYPE_GENERICS


def is_type_operand(e: ast.expr) -> bool:
    return isinstance(e, (ast.Name, ast.Attribute)) or (isinstance(e, ast.Constant) and (e.value is None or isinstance(e.value, str))) \
        or is_type_expr(e)


def is_capwords(name: str) -> bool:
    return name[:1].isupper() and not name.isupper()


def type_definition(node: ast.stmt) -> tuple[str, str] | None:
    """(name, kind) when a module-level statement defines a type, else None."""
    type_stmt = getattr(ast, "TypeAlias", None)  # Python 3.12+
    if type_stmt is not None and isinstance(node, type_stmt) and isinstance(node.name, ast.Name):
        return node.name.id, "type"
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None \
            and last_name(node.annotation) == "TypeAlias":
        return node.target.id, "type"
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        name, v = node.targets[0].id, node.value
        if isinstance(v, ast.Call) and last_name(v.func) in TYPE_FACTORIES:
            return name, TYPE_FACTORIES[last_name(v.func)]  # type: ignore[index]
        if is_capwords(name) and is_type_expr(v):
            return name, "type"
    return None


class Refs(ast.NodeVisitor):
    """Collect (binding key, count) for names and module-attribute chains in a subtree."""

    def __init__(self, bindings: dict[str, tuple]) -> None:
        self.bindings = bindings
        self.found: Counter[tuple] = Counter()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in self.bindings and self.bindings[cur.id][0] == "module":
            self.found[("chain", self.bindings[cur.id][1] + "." + ".".join(reversed(chain)))] += 1
            return
        if isinstance(cur, ast.Name) and cur.id in self.bindings and self.bindings[cur.id][0] == "from":
            # ``from pkg import mod`` then ``mod.thing``: the attribute is kept so that, if the name
            # turns out to be a module (resolve.py decides), the reference lands on ``thing`` in it
            _, mod, name = self.bindings[cur.id]
            self.found[("from", mod, name, chain[-1])] += 1
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        b = self.bindings.get(node.id)
        if b is not None and isinstance(node.ctx, ast.Load):
            self.found[b] += 1

    # import statements bind names; they are not references
    def visit_Import(self, node: ast.Import) -> None:
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        pass


def run(ctx: Context) -> None:
    m = ctx.model
    aliases_by_file: dict[str, dict[str, tuple[str, str]]] = {}
    pending: Counter[tuple[str, tuple]] = Counter()  # (src entity, binding) -> count
    for rel in ctx.sources("python"):
        fid = address.repo(rel)
        tree = ctx.tree(rel)
        if tree is None:
            continue
        doc = doc_summary(tree)
        if doc:
            m.entities[fid].attrs["doc"] = doc
        pkg_parts = rel.split("/")[:-1]
        bindings: dict[str, tuple] = {}
        aliases: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        bindings[a.asname] = ("module", a.name)
                    else:
                        bindings[a.name.split(".")[0]] = ("module", a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    mod = node.module or ""
                else:
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                    mod = ".".join(base + ([node.module] if node.module else []))
                if not mod:
                    continue
                for a in node.names:
                    if a.name == "*":
                        continue
                    bindings[a.asname or a.name] = ("from", mod, a.name)
                    if node in tree.body:
                        aliases[a.asname or a.name] = (mod, a.name)
        aliases_by_file[rel] = aliases
        symbols: dict[str, str] = {}  # classes and type definitions: the file's symbol entities
        for node in tree.body:
            defined = (node.name, "class") if isinstance(node, ast.ClassDef) else type_definition(node)
            if defined:
                name, kind = defined
                sid = address.part(fid, name)
                attrs = {"path": rel, "line": node.lineno, "end_line": node.end_lineno, "language": "python"}
                if kind == "class" and doc_summary(node):
                    attrs["doc"] = doc_summary(node)
                m.add_entity(Entity(sid, kind, name, attrs, Provenance("python-symbols", rel, node.lineno)))
                m.contain(fid, sid, "python-symbols")
                symbols[name] = sid
            elif isinstance(node, FUNC_DEFS):
                bindings[node.name] = ("local", fid, node.name)  # a module's functions are the module's own body
        for name, sid in symbols.items():
            bindings[name] = ("local", sid, name)
        m.entities[fid].attrs.update(own_code(tree, rel, pkg_parts, aliases, m.entities[fid].attrs.get("lines")))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            defined = (node.name, "class") if isinstance(node, ast.ClassDef) else type_definition(node)
            src = symbols[defined[0]] if defined else fid
            v = Refs(bindings)
            v.visit(node)
            for b, n in v.found.items():
                if b[0] == "local" and b[1] == src:
                    continue
                pending[(src, b)] += n
    ctx.notes["py-aliases"] = aliases_by_file
    for (src, b), n in pending.items():
        name = {"local": lambda: b[2], "from": lambda: b[2], "module": lambda: b[1], "chain": lambda: b[1].rsplit(".", 1)[-1]}[b[0]]()
        if b[0] == "from" and len(b) == 4:  # ``name.attr``: resolve.py takes ``attr`` when name is a module
            name = f"{b[2]}.{b[3]}"
        if b[0] == "local":
            m.add_relation(Relation(USE, src, b[1], "ref", n, "exact", Provenance("python-symbols", address.path_of(src)),
                                    {"name": name}))
            continue
        key = f"{b[1]}:{name}" if b[0] == "from" else f"{b[1]}:"
        uid = address.unresolved("pyref", key)
        if not m.has(uid):
            m.add_entity(Entity(uid, "unresolved", key, {"language": "python", "ref": True}, Provenance("python-symbols")))
        m.add_relation(Relation(USE, src, uid, "ref", n, "exact", Provenance("python-symbols"), {"name": name}))
