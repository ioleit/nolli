"""What the viewer shows when the pointer rests on a box or an edge: plain sentences.

A description is a list of lines; a line is a list of parts, plain strings and
{"v": value} for the data in it ("api-mock references 1 name in lib/"). The viewer sets
the values apart. One sentence each for now; more lines can be added.
"""

from __future__ import annotations

from .model import Model
from .rollup import RolledUp
from .labels import is_body, label_of, leaf_count

LANGUAGE = {"python": "Python", "typescript": "TypeScript", "javascript": "JavaScript", "shell": "shell", "go": "Go",
            "rust": "Rust", "java": "Java", "js": "JavaScript", "py": "Python"}


def say(template: str, *values) -> list:
    """A sentence as parts: the template's text, and each {} filled with a value part."""
    parts: list = []
    for i, text in enumerate(template.split("{}")):
        if text:
            parts.append(text)
        if i < len(values):
            parts.append({"v": str(values[i])})
    return parts


def count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def end_label(m: Model, x: str, expanded: set[str]) -> str:
    """A box as the picture names it: directories end in "/", a module's own code says so."""
    if is_body(x):
        return f"{end_label(m, x[5:], expanded)} (own code)"
    e = m.entities[x]
    if e.kind in ("service", "image"):
        return e.name.removesuffix("/Dockerfile")  # no "svc " / "img " prefix in prose
    if e.kind == "endpoint":
        return f"{e.attrs.get('method', '')} {e.attrs.get('path', e.name)}".strip()
    label = label_of(m, x, expanded)
    return label + "/" if e.kind == "directory" and not label.endswith("/") else label


def node_info(m: Model, x: str, rolled: RolledUp, expanded: set[str], utilities: set[str], container: bool) -> list:
    if is_body(x):
        owner = end_label(m, x[5:], expanded)
        if m.entities[x[5:]].kind == "directory":
            return [say("The code in {}'s __init__.py beyond re-exporting its modules", owner)]
        return [say("The code in {} outside any class", owner)]
    e = m.entities[x]
    name, kids = end_label(m, x, expanded), m.children(x)
    lang = LANGUAGE.get(e.attrs.get("language") or "", "")
    if e.kind == "directory":
        files = leaf_count(m, x)
        folders = sum(1 for c in kids if m.entities[c].kind == "directory")
        if folders:
            return [say("{} holds {} in {}", name, count(files, "file"), count(folders, "folder"))]
        return [say("{} holds {}", name, count(files, "file"))]
    if e.kind == "file":
        if kids:
            return [say("{} is a " + (lang + " " if lang else "") + "module with {}", name, count(len(kids), "class", "classes"))]
        return [say("{} is a " + lang + " module", name)] if lang else [say("{} is a file", name)]
    if e.kind == "class":
        where = (e.attrs.get("path") or "").rsplit("/", 1)[-1]
        return [say("{} is a class in {}", name, where)] if where else [say("{} is a class", name)]
    if e.kind == "group":
        return [say("{} holds {}", name, count(len(kids), "entry", "entries"))]
    what = {"endpoint": "an HTTP endpoint", "env": "an environment variable", "service": "a Compose service",
            "image": "a Docker image", "package": "an external package", "host": "a host"}.get(e.kind, "a " + e.kind)
    return [say("{} is " + what, name)]


# per edge subkind: a sentence with {} for source, count and target
COUNTED = {
    "import": ("{} references {} in {}", "name"),
    "http": ("{} makes {} to endpoints served by {}", "call"),
    "endpoint": ("{} makes {} to endpoints served by {}", "call"),
    "env": ("{} reads {} set by {}", "variable"),
}
# deployment relations: a sentence with {} for source and target
PLAIN = {
    "depends_on": "{} depends on {}",
    "url": "{} has a URL pointing at {}",
    "copies": "{} is copied into {}",
    "entrypoint": "{} starts {}",
    "runs_as": "{} runs as {}",
}


def edge_info(m: Model, e, expanded: set[str], local_in: str | None, weak: bool) -> list:
    src, dst = end_label(m, e.src, expanded), end_label(m, e.dst, expanded)
    sub = e.subkind.removesuffix("-dangling")
    dangling = e.subkind.endswith("-dangling")
    if e.kind == "provide":
        return [say("{} serves endpoints that nothing here calls", dst)] if dangling else [say("{} is served by {}", src, dst)]
    if dangling:
        return [say("{} reads {} that nothing here sets", src, count(e.count, "variable"))] if sub == "env" \
            else [say("{} uses {} that nothing here provides", src, count(e.count, "interface"))]
    if sub in COUNTED:
        template, noun = COUNTED[sub]
        return [say(template, src, count(e.count, noun), dst)]
    return [say(PLAIN.get(sub, "{} uses {}"), src, dst)]
