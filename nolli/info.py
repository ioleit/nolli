"""What the viewer shows when the pointer rests on a box or an edge, in plain words.

Each description is structured, not a string, so the viewer can lay it out and set data
apart from prose:

    kind     what sort of thing it is ("Python module", "HTTP endpoint")
    title    its name as the picture shows it
    path     where it lives in the repository, if anywhere
    summary  one sentence (edges: what the arrow means)
    facts    label/value pairs
    list     a labelled list (the endpoints or variables an edge goes through)
    hints    short sentences about how it is drawn or what a click does

summary and hints are lists of parts: plain strings, and {"v": value} for data.
"""

from __future__ import annotations

from .model import Model
from .rollup import RolledUp, is_init
from .labels import is_body, label_of, leaf_count

LANGUAGE = {"python": "Python", "typescript": "TypeScript", "javascript": "JavaScript", "shell": "Shell", "go": "Go",
            "rust": "Rust", "java": "Java", "js": "JavaScript", "py": "Python"}
ROLE = {"source": "source code", "mixed": "source code with other files", "test": "tests", "docs": "documentation",
        "config": "configuration", "markup": "markup (HTML, CSS)", "data": "data", "asset": "assets", "other": "other files"}
GROUP = {"ext": "External packages", "env": "Environment variables", "deploy": "Deployment", "http": "HTTP API"}
LIST_MAX = 12

# per edge subkind: (kind shown, summary template with {} for source and target, what a count counts)
EDGE = {
    "import": ("Uses", "{} uses names defined in {}", "references"),
    "http": ("HTTP calls", "{} calls HTTP endpoints served by {}", "calls"),
    "endpoint": ("HTTP calls", "{} calls HTTP endpoints served by {}", "calls"),
    "env": ("Environment", "{} reads environment variables that {} sets", "reads"),
    "depends_on": ("Deployment", "{} depends on {} (Compose)", "declarations"),
    "url": ("Deployment", "{} is configured with a URL that points at {}", "URLs"),
    "copies": ("Deployment", "{} is copied into {} (Dockerfile COPY)", "copies"),
    "entrypoint": ("Deployment", "{} starts {} (Dockerfile entry point)", "entry points"),
    "runs_as": ("Deployment", "The image {} runs as {}", "services"),
}


def say(template: str, *values) -> list:
    """A sentence as parts: the template's text, and each {} filled with a value part."""
    parts: list = []
    pieces = template.split("{}")
    for i, text in enumerate(pieces):
        if text:
            parts.append(text)
        if i < len(values):
            parts.append({"v": str(values[i])})
    return parts


def kib(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n} B"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def end_label(m: Model, x: str, expanded: set[str]) -> str:
    """An edge end as the picture names it: directories end in "/", own code says so."""
    if is_body(x):
        return f"{end_label(m, x[5:], expanded)} (own code)"
    e = m.entities[x]
    if e.kind in ("service", "image"):
        return e.name.removesuffix("/Dockerfile")  # the kind says what it is; no "svc " / "img " prefix
    label = label_of(m, x, expanded)
    return label + "/" if e.kind == "directory" and not label.endswith("/") else label


def node_kind(m: Model, x: str) -> str:
    e = m.entities[x]
    lang = LANGUAGE.get(e.attrs.get("language") or "", "")
    if e.kind == "directory":
        return "Repository" if e.attrs.get("root") else ("Python package" if any(is_init(m, c) for c in m.children(x)) else "Directory")
    if e.kind == "file":
        return f"{lang} module" if lang and e.attrs.get("role") in ("source", None) else (f"{lang} file" if lang else "File")
    return {"class": f"{lang} class".strip(), "group": "Group", "endpoint": "HTTP endpoint", "env": "Environment variable",
            "package": f"External {lang} package".replace("  ", " ") if lang else "External package",
            "service": "Compose service", "image": "Docker image", "host": "Host"}.get(e.kind, e.kind.capitalize())


def node_info(m: Model, x: str, rolled: RolledUp, expanded: set[str], utilities: set[str], container: bool) -> dict:
    if is_body(x):
        o = m.entities[x[5:]]
        package = o.kind == "directory"
        return {
            "kind": "Package's own code" if package else "Module's own code",
            "title": end_label(m, x[5:], expanded),
            "path": f"{o.attrs.get('path')}/__init__.py" if package else o.attrs.get("path"),
            "summary": say("What {} does beyond re-exporting its modules", end_label(m, x[5:], expanded)) if package
            else say("Functions, constants and statements of {} outside any class", end_label(m, x[5:], expanded)),
            "facts": [], "list": None, "hints": [],
        }
    e = m.entities[x]
    kids = m.children(x)
    title = end_label(m, x, expanded) if e.kind in ("service", "image") else label_of(m, x, expanded)
    if e.kind == "endpoint":
        title = f"{e.attrs.get('method', '')} {e.attrs.get('path', e.name)}".strip()
    elif e.kind == "directory" and not e.attrs.get("root"):
        title += "/"
    facts: list[dict] = []
    fact = lambda label, value: facts.append({"label": label, "value": str(value)})  # noqa: E731
    if e.kind == "class" and e.attrs.get("line"):
        fact("Defined at", f"{e.attrs.get('path')}:{e.attrs['line']}")
    if e.kind == "directory":
        files = leaf_count(m, x)
        subdirs = sum(1 for c in kids if m.entities[c].kind == "directory")
        fact("Contains", ", ".join(filter(None, [plural(files, "file"), plural(subdirs, "folder") if subdirs else ""])))
    elif e.kind == "file" and kids:
        fact("Classes", len(kids))
    elif e.kind == "group":
        fact("Contains", plural(len(kids), "entry", "entries"))
    langs = e.attrs.get("languages")
    if langs:
        top = sorted(langs.items(), key=lambda kv: -kv[1])[:3]
        fact("Languages", " · ".join(f"{LANGUAGE.get(k, k)} {kib(v)}" for k, v in top))
    elif e.attrs.get("language") and e.kind not in ("class",):
        fact("Language", LANGUAGE.get(e.attrs["language"], e.attrs["language"]))
    if e.attrs.get("role") and e.kind in ("directory", "file"):
        fact("Role", ROLE.get(e.attrs["role"], e.attrs["role"]))
    if kids and rolled.internal.get(x):
        fact("Links between its parts", rolled.internal[x])
    for g, c in sorted((rolled.annotations.get(x) or {}).items()):
        if g.startswith("util:"):
            fact(f"Uses {end_label(m, g[5:], expanded)}", plural(c, "reference"))
        else:
            fact(GROUP.get(g, m.entities[g].name if m.has(g) else g), plural(c, "link"))
    if rolled.allocations.get(x):
        fact("Deployed in", ", ".join(m.entities[s].name for s in rolled.allocations[x]))
    for key, label in (("image", "Image"), ("dockerfile", "Dockerfile"), ("compose", "Defined in")):
        if e.attrs.get(key):
            fact(label, e.attrs[key])
    if e.attrs.get("ports"):
        fact("Ports", ", ".join(map(str, e.attrs["ports"])))
    hints = []
    if x in utilities:
        hints.append(["A utility: so many arrows point at it that they are left out; the boxes using it list it instead"])
    if container:
        hints.append(["Click − to fold"])
    elif kids:
        hints.append(say("Click to unfold its {}", plural(len(kids), "part")))
    path = e.attrs.get("path") if e.kind not in ("class", "endpoint") else None
    return {"kind": node_kind(m, x), "title": title, "path": path if path not in (None, ".") else None,
            "summary": None, "facts": facts, "list": None, "hints": hints}


def interface_name(iface: str) -> str:
    """'http:GET /api/x' -> 'GET /api/x', 'env:PORT' -> 'PORT'."""
    return iface.split(":", 1)[1] if ":" in iface else iface


def edge_info(m: Model, e, expanded: set[str], local_in: str | None, weak: bool) -> dict:
    src, dst = end_label(m, e.src, expanded), end_label(m, e.dst, expanded)
    sub = e.subkind.removesuffix("-dangling")
    dangling = e.subkind.endswith("-dangling")
    if e.kind == "provide":
        kind, summary, counts = "Implements", say("{} is served by {}", src, dst), "routes"
        if dangling:
            summary = say("{} serves endpoints that nothing in the repository calls", dst)
    else:
        kind, template, counts = EDGE.get(sub, ("Uses", "{} uses {}", "links"))
        summary = say(template, src, dst)
        if dangling:
            summary = say("{} reads environment variables that nothing in the repository sets", src) if sub == "env" \
                else say("{} uses interfaces that nothing in the repository provides", src)
    facts = [{"label": counts[:1].upper() + counts[1:], "value": str(e.count)}]
    if e.confidence != "exact":
        facts.append({"label": "Match", "value": "guessed from the URL's shape" if sub in ("http", "endpoint") or e.kind == "provide"
                      else e.confidence})
    fans = [int(v[4:]) for v in e.via if v.startswith("fan:")]
    if fans:
        facts.append({"label": "Bundled", "value": f"{sum(fans)} arrows drawn as one, at the box's edge"})
    ifaces = sorted(interface_name(v) for v in e.via if not v.startswith("fan:"))
    lst = None
    if ifaces:
        label = {"http": "Endpoints", "endpoint": "Endpoints", "env": "Variables"}.get(sub, "Through")
        lst = {"label": label, "items": ifaces[:LIST_MAX], "more": max(0, len(ifaces) - LIST_MAX)}
    hints = []
    if local_in:
        hints.append(say("Inside {}: drawn faint", end_label(m, local_in, expanded)))
    if weak:
        hints.append(say("A small part of what {} uses: drawn faint", src))
    return {"kind": kind, "title": f"{src} → {dst}", "path": None, "summary": summary, "facts": facts, "list": lst,
            "hints": hints}
