"""Mermaid export of a rolled view: the static artefact for READMEs and PRs."""

from __future__ import annotations

import re

from .changes import is_diff, node_diff
from .rollup import HIDDEN_ROLES_DEFAULT, RolledUp, chain_end, display_path, hidden_by_role, image_aliases, is_init
from .model import Model

ARROW = {"import": "-->", "ref": "-->", "http": "==>", "endpoint": "==>", "env": "-.->", "depends_on": "-->", "url": "-->",
         "runs_as": "-.->", "copies": "-.->", "entrypoint": "-.->"}
GROUP_SHORT = {"ext": "pkgs", "env": "env", "deploy": "deploy", "api": "api"}


MODULE_LANGS = ("python", "typescript", "javascript")


def module_name(e) -> str:
    """A source file is shown as its module: name without the extension."""
    if e.kind == "file" and e.attrs.get("language") in MODULE_LANGS:
        return e.name.rsplit(".", 1)[0]
    return e.name


def nid(eid: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", eid)


def label(m: Model, eid: str, ann: dict[str, int] | None, expanded: set[str] = frozenset(), alloc: list[str] | None = None,
          show_files: dict[str, int] | None = None) -> str:
    show_files = show_files or {}
    if eid.startswith("body:"):
        base = module_name(m.entities[eid[5:]])  # the module's own code: the module itself
    else:
        e = m.entities[eid]
        if e.kind == "directory":
            base = (e.name if e.attrs.get("root") else display_path(m, eid, expanded)) + "/"
        elif e.kind == "file":
            base = module_name(e)
        elif e.kind == "class":
            base = f"class {e.name}"
        elif e.kind == "service":
            base = f"svc {e.name}"
        elif e.kind == "image":
            base = f"image {e.name}"
        else:
            base = e.name
    if ann:
        parts = []
        for g, c in sorted(ann.items()):
            if g.startswith("util:"):
                t = g[5:]
                parts.append("→ " + (m.entities[t].name + "/" if m.has(t) and m.entities[t].kind == "directory" else m.entities[t].name if m.has(t) else t))
            else:
                parts.append(f"{GROUP_SHORT.get(g, g)} {c}")
        base += " (" + ", ".join(parts) + ")"
    if alloc:
        base += " [" + ", ".join(s.removeprefix("svc:") for s in alloc) + "]"
    return base.replace('"', "'")


def render(m: Model, view: dict, rolled: RolledUp) -> str:

    diff = is_diff(m)
    status_of = lambda n: ((node_diff(m, n, rolled.internal_changes.get(n)) or {}).get("status") if diff else None)  # noqa: E731
    expanded = set(view["expanded"])
    show_files = dict(view.get("show_files") or {})
    visible = set(rolled.visible)
    hidden = hidden_by_role(m, view.get("hidden_roles") or HIDDEN_ROLES_DEFAULT) | set(image_aliases(m))
    touched = {e.src for e in rolled.edges} | {e.dst for e in rolled.edges}
    out = ["flowchart TD"]
    classed: list[tuple[str, str]] = []  # subgraphs take classes through `class`, not `:::`

    def emit(n: str, indent: int) -> None:
        pad = "  " * indent
        e = m.entities[n]
        if n in expanded and m.children(n):
            out.append(f'{pad}subgraph {nid(n)}_g ["{label(m, n, None, expanded)}"]')
            if status_of(n):
                classed.append((f"{nid(n)}_g", status_of(n)))
            if f"body:{n}" in visible:
                out.append(f'{pad}  {nid("body:" + n)}(("{label(m, "body:" + n, rolled.annotations.get("body:" + n))}"))')
            for c in m.children(n):
                if e.kind == "directory" and is_init(m, c):
                    for cc in m.children(c):  # the package's own classes
                        if cc in visible:
                            emit(cc, indent + 1)
                    continue
                if e.kind == "directory" and m.entities[c].kind == "directory":
                    c = chain_end(m, c, hidden)
                if c in visible or (c in expanded and m.children(c)):
                    emit(c, indent + 1)
            out.append(f"{pad}end")
        elif n in visible:
            if e.kind == "group" and m.parent(n) is None and n not in touched:
                return  # folded root groups appear as annotations on the nodes that use them
            cls = ":::ext" if e.kind == "package" else ":::deploy" if e.kind in ("service", "image", "host") else ":::group" if e.kind == "group" else ":::symbol" if e.kind in ("class", "function") else ""
            st = status_of(n)
            out.append(f'{pad}{nid(n)}["{label(m, n, rolled.annotations.get(n), expanded, rolled.allocations.get(n))}"]{":::" + st if st else cls}')

    for r in m.roots():
        emit(r, 1)
    out.append("  classDef ext stroke-dasharray:5 5,fill:#fafafa,color:#555")
    out.append("  classDef deploy fill:#eef5ff,stroke:#6a8fd8")
    out.append("  classDef files fill:#f4f4f4,stroke:#bbb,color:#666")
    out.append("  classDef group stroke-dasharray:2 3")
    out.append("  classDef symbol fill:#fff8e6,stroke:#c9a227")
    if diff:
        out.append("  classDef added fill:#e6f6ea,stroke:#2e9b4f,stroke-width:2px")
        out.append("  classDef removed fill:#fdecec,stroke:#c94040,stroke-width:2px,stroke-dasharray:5 3,color:#a33")
        out.append("  classDef changed stroke:#d08a1a,stroke-width:2px")
        for g, st in classed:
            out.append(f"  class {g} {st}")
    def ref(n: str) -> str:
        return nid(n) + "_g" if n in expanded and m.children(n) else nid(n)

    styles: list[str] = []
    for i, e in enumerate(rolled.edges):
        arrow = "-.->" if e.kind == "provide" else ARROW.get(e.subkind.replace("-dangling", ""), "-->")
        lab = []
        st = e.status if diff else None
        if st:
            lab.append(f"{st} {e.base}→{e.head}")
            styles.append(f"  linkStyle {i} stroke:{ {'added': '#2e9b4f', 'removed': '#c94040', 'changed': '#d08a1a'}[st] },stroke-width:2px"
                          + (",stroke-dasharray:5 3" if st == "removed" else ""))
        elif e.count > 1:
            lab.append(str(e.count))
        if e.kind == "provide":
            lab.append("realised by")
        elif e.subkind not in ("import", "ref"):
            lab.append("http" if e.subkind == "endpoint" else e.subkind)
        text = f"|{' '.join(lab)}|" if lab else ""
        out.append(f"  {ref(e.src)} {arrow}{text} {ref(e.dst)}")
    return "\n".join(out + styles) + "\n"
