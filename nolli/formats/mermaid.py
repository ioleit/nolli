"""Exports a scene as Mermaid."""

from __future__ import annotations

import re

from nolli.model import address
from nolli.model.scene import Scene

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


make_node_id = address.format_identifier


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


def list_drawing_order(scene: Scene) -> list[str]:
    """The nodes in the order they are drawn: a projection's (each container before what it
    holds), else down the containment tree; nodes the model no longer has are left out."""
    m = scene.model
    order = [x for x in m.meta.get("projection", {}).get("order") or [] if x in m.kinds]
    if len(order) == len(m.kinds):
        return order
    seen, out = set(order), list(order)
    stack = [x for x in reversed(m.list_roots())]
    while stack:
        x = stack.pop()
        if x not in seen:
            seen.add(x)
            out.append(x)
        stack.extend(reversed(m.list_children(x)))
    return out


def read_scene(scene: Scene) -> tuple[list[dict], list[dict], bool]:
    """A projected scene's boxes (drawing order) and edges, as the drawing needs them."""
    order = list_drawing_order(scene)
    nodes = []
    for x in order:
        el = scene.find_element(x)
        p, lab = el.get("projection") or {}, el.get("label") or {}
        nodes.append({"id": x, "kind": el.kind, "parent": scene.model.find_parent(x), "container": p.get("container"),
                      "label": lab.get("text", ""), "icons": lab.get("icons", []), "chips": p.get("chips", []),
                      "badge": p.get("badge", ""), "utility": p.get("utility"), "utilities_used": p.get("utilities_used", []),
                      "diff": el.get("change")})
    edges = []
    for el in scene.list_edges():
        p = el.get("projection") or {}
        edges.append({"src": el.key.src, "dst": el.key.dst, "subkind": el.key.subkind, "count": p.get("count", 0),
                      "style": p.get("style"), "types": p.get("types"), "local": p.get("local"), "weak": p.get("weak"),
                      "diff": el.get("change")})
    return nodes, edges, "change" in scene.model.meta  # a picture of a change


def list_ids(scene: Scene) -> dict[str, str]:
    """The Mermaid id of each box ``render`` draws (an unfolded one is a subgraph, ``<id>_g``),
    to the scene id it stands for: how a client maps a click on the drawing back to the scene."""
    nodes, _, _ = read_scene(scene)
    return {make_node_id(n["id"]) + ("_g" if n.get("container") else ""): n["id"] for n in nodes}


def render(scene: Scene, fade: bool = True) -> str:
    """The scene as a Mermaid flowchart. ``fade``: in a change's scene, draw what did not
    change faint (the viewer's "fade unchanged")."""
    node_list, edge_list, review = read_scene(scene)
    nodes = {n["id"]: n for n in node_list}
    kids: dict[str | None, list[dict]] = {}
    for n in node_list:
        kids.setdefault(n["parent"], []).append(n)
    ref = lambda x: make_node_id(x) + "_g" if nodes[x].get("container") else make_node_id(x)
    status = lambda x: (x.get("diff") or {}).get("status")
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
        out.append(pad + make_node_id(n["id"]) + shape.format(label(n)) + (f":::{cls}" if cls else ""))
        if n.get("utility"):
            classes.append((make_node_id(n["id"]), "utility"))

    for n in kids.get(None, []):
        emit(n, 1)

    styles: dict[str, list[int]] = {}
    links = 0
    for e in edge_list:
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
    for n in node_list:
        if not n.get("container"):
            for u in n.get("utilities_used") or []:
                if u in nodes and u != n["id"] and u not in below:
                    out.append(f"  {make_node_id(n['id'])} ~~~ {ref(u)}")
                    below.add(u)

    used = sorted({c for _, c in classes} | {m.group(1) for line in out if (m := re.search(r":::(\w+)$", line))})
    out += [f"  classDef {c} {CLASS_DEF[c]}" for c in used]
    out += [f"  class {x} {c}" for x, c in classes]
    out += [f"  linkStyle {','.join(map(str, ix))} {style}" for style, ix in styles.items()]
    return "\n".join(out) + "\n"
