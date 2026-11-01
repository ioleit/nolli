"""Mermaid of a scene: the second renderer, for READMEs, PR comments and agents.

It draws the scene the viewer draws (``scene.py``): the same boxes, containers and edges,
with Mermaid's own layout, top to bottom. Only what plain Mermaid can say, so that the text
renders anywhere (GitHub included) and stays readable as text: no click handlers, no
layout engines beyond the default, styles through ``classDef`` and ``linkStyle`` only.
Where the viewer pins utilities to the bottom, an invisible link (``~~~``) from a box that
uses one keeps it below that box.
"""

from __future__ import annotations

import re

STYLE_ARROW = {"import": "-->", "http": "==>", "env": "-.->", "deploy": "-.->", "provide": "-.->"}
KIND_CLASS = {"package": "ext", "service": "deploy", "image": "deploy", "host": "deploy", "group": "group",
              "class": "symbol", "interface": "symbol", "type": "symbol", "enum": "symbol", "function": "symbol"}
ICON_WORD = {"C": "class", "I": "interface", "T": "type", "E": "enum", "f": "function", "img": "image"}
SILENT_ICONS = {"D", "m", "/"}  # a directory ends in "/", a module and a path need no word
# a fill comes with a text colour: GitHub draws Mermaid dark in dark mode
CLASS_DEF = {
    "ext": "stroke-dasharray:5 5",
    "deploy": "fill:#eef5ff,stroke:#6a8fd8,color:#222",
    "group": "stroke-dasharray:2 3",
    "symbol": "fill:#fff8e6,stroke:#c9a227,color:#222",
    "utility": "stroke-dasharray:1 3",
    "added": "fill:#e6f6ea,stroke:#2e9b4f,stroke-width:2px,color:#222",
    "removed": "fill:#fdecec,stroke:#c94040,stroke-width:2px,stroke-dasharray:5 3,color:#a33",
    "changed": "stroke:#d08a1a,stroke-width:2px",
    "same": "opacity:0.45",
}
STATUS_STROKE = {"added": "#2e9b4f", "removed": "#c94040", "changed": "#d08a1a"}


def nid(eid: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", eid)


def text(s: str) -> str:
    """A label as Mermaid reads it between double quotes."""
    return s.replace('"', "'").replace("<", "#lt;").replace(">", "#gt;")


def label(n: dict) -> str:
    if n["kind"] == "anchor":
        return "own code"
    words = [ICON_WORD.get(i, i) for i in n["icons"] if i not in SILENT_ICONS]
    out = text(" ".join(words + [n["label"] + ("/" if n["kind"] == "directory" else "")]))
    if n["chips"]:
        out += text(" [" + ", ".join(c["name"] for c in n["chips"]) + "]")
    if n["badge"]:
        out += "<br/>" + text(n["badge"])
    return out


def render(sc: dict, fade: bool = True) -> str:
    """The scene as a Mermaid flowchart. ``fade``: in a change's scene, draw what did not
    change faint (the viewer's "fade unchanged")."""
    review = "diff" in sc
    nodes = {n["id"]: n for n in sc["nodes"]}
    kids: dict[str | None, list[dict]] = {}
    for n in sc["nodes"]:
        kids.setdefault(n["parent"], []).append(n)
    ref = lambda x: nid(x) + "_g" if nodes[x].get("container") else nid(x)  # noqa: E731
    status = lambda x: (x.get("diff") or {}).get("status")  # noqa: E731
    out = ["flowchart TD"]
    classes: list[tuple[str, str]] = []  # given after the nodes: subgraphs take them only that way

    def emit(n: dict, indent: int) -> None:
        pad = "  " * indent
        st = status(n) or ("same" if review and fade else None)
        if n.get("container"):
            out.append(f'{pad}subgraph {ref(n["id"])} ["{label(n)}"]')
            for c in kids.get(n["id"], []):
                emit(c, indent + 1)
            out.append(f"{pad}end")
            if st:
                classes.append((ref(n["id"]), st))
            return
        cls = st or KIND_CLASS.get(n["kind"])
        shape = '(("{}"))' if n["kind"] == "anchor" else '["{}"]'
        out.append(pad + nid(n["id"]) + shape.format(label(n)) + (f":::{cls}" if cls else ""))
        if n.get("utility"):
            classes.append((nid(n["id"]), "utility"))

    for n in kids.get(None, []):
        emit(n, 1)

    styles: dict[str, list[int]] = {}
    links = 0
    for e in sc["edges"]:
        if e["src"] not in nodes or e["dst"] not in nodes:
            continue
        d = e.get("diff")
        lab = []
        if d:
            lab.append(f"{d['status']} {d['base']}→{d['head']}")
        elif e["count"] > 1:
            lab.append(str(e["count"]))
        if e["style"] == "provide":
            lab.append("realised by")
        elif e["subkind"] not in ("import", "ref"):
            lab.append("http" if e["subkind"] == "endpoint" else e["subkind"])
        arrow = "-.->" if e["types"] and e["style"] == "import" else STYLE_ARROW.get(e["style"], "-->")
        out.append(f"  {ref(e['src'])} {arrow}{'|' + ' '.join(lab) + '|' if lab else ''} {ref(e['dst'])}")
        style = (f"stroke:{STATUS_STROKE[d['status']]},stroke-width:2px" + (",stroke-dasharray:5 3" if d["status"] == "removed" else "") if d
                 else "stroke-opacity:0.2" if review and fade
                 else "stroke-opacity:0.35,stroke-width:0.7px" if e["local"] or e["weak"] else None)
        if style:
            styles.setdefault(style, []).append(links)
        links += 1
    # a utility below a box that uses it: its incoming edges are not drawn, and Mermaid would
    # put a box without them at the top. One link each, from the first box that uses it.
    below = set()
    for n in sc["nodes"]:
        if not n.get("container"):
            for u in n.get("utilities_used") or []:
                if u in nodes and u != n["id"] and u not in below:
                    out.append(f"  {nid(n['id'])} ~~~ {ref(u)}")
                    below.add(u)

    used = sorted({c for _, c in classes} | {m.group(1) for line in out if (m := re.search(r":::(\w+)$", line))})
    out += [f"  classDef {c} {CLASS_DEF[c]}" for c in used]
    out += [f"  class {x} {c}" for x, c in classes]
    out += [f"  linkStyle {','.join(map(str, ix))} {style}" for style, ix in styles.items()]
    return "\n".join(out) + "\n"
