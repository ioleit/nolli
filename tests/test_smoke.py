"""End-to-end smoke test on a synthetic mixed repository.

Builds a tiny FastAPI backend, a React-ish frontend that calls it through a
helper, a docker-compose file and a Dockerfile, then checks that the model
binds the frontend call to the route, the directory to the image, the env read
to the service, and that the default cut lifts a frontend -> backend edge.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.build import extract
from nolli.rollup import bundle, hidden_by_role, image_aliases, rollup
from nolli.view import default_view

FILES = {
    "backend/__init__.py": "",
    "backend/main.py": (
        "import os\nfrom fastapi import FastAPI\nfrom .api import worlds\n"
        "app = FastAPI()\napp.include_router(worlds.router, prefix='/api')\n"
        "REDIS = os.environ['REDIS_URL']\n"
    ),
    "backend/api/__init__.py": "",
    "backend/api/worlds.py": (
        "from fastapi import APIRouter\nfrom ..store import Store\n"
        "router = APIRouter(prefix='/worlds')\n"
        "@router.get('/{world}/echoes')\ndef echoes(world: str): return Store().list(world)\n"
        "@router.post('')\ndef create(): return {}\n"
    ),
    "backend/store.py": "class Store:\n    def list(self, w): return []\n",
    "backend/tests/test_store.py": "from backend.store import Store\n",
    "frontend/src/lib/api.ts": (
        "const API_BASE = import.meta.env.VITE_API_BASE || '/api';\n"
        "export async function fetchJson(path: string) { return (await fetch(`${API_BASE}${path}`)).json(); }\n"
    ),
    "frontend/src/App.tsx": (
        "import { fetchJson } from './lib/api';\nimport React from 'react';\n"
        "export function App() { return fetchJson(`/worlds/${w}/echoes`); }\n"
    ),
    "frontend/src/main.tsx": "import { App } from './App';\n",
    "docker-compose.yml": (
        "services:\n  api:\n    build: {context: ., dockerfile: backend/Dockerfile}\n"
        "    environment:\n      REDIS_URL: redis://redis:6379\n    depends_on: [redis]\n"
        "  redis:\n    image: redis:7\n"
    ),
    "backend/Dockerfile": "FROM python:3.12\nCOPY backend/ backend/\nCMD [\"uvicorn\", \"backend.main:app\"]\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def rel_set(m, kind, subkind=None):
    return {(r.src, r.dst) for r in m.relations if r.kind == kind and (subkind is None or r.subkind == subkind)}


def test_model_binds_the_seams(repo: Path):
    m, ctx = extract(repo)
    uses = rel_set(m, "use")
    # imports resolve inside the repo, relative and absolute; Python references bind at
    # symbol level (module-level code -> a module variable stays at file level)
    assert ("fs:backend/main.py", "fs:backend/api/worlds.py") in uses
    # the function ``echoes`` is the module's own body, so the reference comes from the module
    assert ("fs:backend/api/worlds.py", "py:backend/store.py#Store") in rel_set(m, "use", "ref")
    assert ("fs:backend/api/worlds.py", "fs:backend/store.py") not in uses, "import edge replaced by the reference"
    assert m.entities["py:backend/store.py#Store"].kind == "class"
    assert not m.has("py:backend/api/worlds.py#echoes"), "functions are not entities"
    assert ("fs:frontend/src/App.tsx", "fs:frontend/src/lib/api.ts") in uses
    # routes get the include prefix; the helper call is bound to the route
    ep = "api:http:backend/main.py:app GET /api/worlds/{world}/echoes"
    assert m.has(ep)
    assert ("fs:frontend/src/App.tsx", ep) in rel_set(m, "use", "http")
    assert ("fs:backend/api/worlds.py", ep) in rel_set(m, "provide")
    # the shared API structure: APIs > the app > its merged path steps > operations by verb
    app = "api:http:backend/main.py:app"
    assert m.parent(app) == "api" and m.entities[app].kind == "api"
    worlds = app + "/api/worlds"
    assert m.parent(worlds) == app and m.entities[worlds].name == "api/worlds"
    assert sorted(m.entities[c].name for c in m.children(worlds)) == ["GET {world}/echoes", "POST"]
    # env read meets env set; compose and Dockerfile give allocation and runtime use
    assert ("fs:backend/main.py", "env:REDIS_URL") in rel_set(m, "use", "env")
    assert ("svc:api", "env:REDIS_URL") in rel_set(m, "provide", "env")
    assert ("fs:backend", "img:backend/Dockerfile") in rel_set(m, "allocate", "copies")
    assert ("img:backend/Dockerfile", "svc:api") in rel_set(m, "allocate", "runs_as")
    assert ("svc:api", "svc:redis") in rel_set(m, "use", "depends_on")
    assert ("svc:api", "svc:redis") in rel_set(m, "use", "url")
    assert ("img:backend/Dockerfile", "fs:backend/main.py") in rel_set(m, "use", "entrypoint")
    # externals, no stdlib, nothing unresolved
    assert m.has("ext:py:fastapi") and m.has("ext:js:react") and not m.has("ext:py:os")
    assert not [e for e in m.entities.values() if e.kind == "unresolved"]
    # tests are role-tagged, not dropped
    assert m.entities["fs:backend/tests/test_store.py"].attrs["role"] == "test"


def test_default_cut_lifts_frontend_to_backend(repo: Path):
    m, _ = extract(repo)
    view = default_view(m, budget=12)
    hidden = hidden_by_role(m) | set(image_aliases(m))
    rolled = rollup(m, set(view["visible"]), hidden, shown_groups={"api"})
    rolled.edges = bundle(m, rolled.edges, set(view["expanded"]))
    calls = [e for e in rolled.edges if e.subkind == "http"]
    assert calls, "frontend -> APIs edge"
    assert all(e.src.startswith("fs:frontend") and e.dst == "api" for e in calls)
    served = [e for e in rolled.edges if e.kind == "provide" and e.src == "api"]
    assert served and all(e.dst.startswith("fs:backend") for e in served)
    # the test directory is hidden, externals are a badge, not an edge
    assert "fs:backend/tests" not in view["visible"]
    assert not any(e.dst == "ext" or e.src == "ext" for e in rolled.edges)
    assert any("ext" in a for a in rolled.annotations.values())


def test_scene_of_default_cut(repo: Path):
    """The scene the viewer draws: stable ids, every node's parent drawn before it, edges
    between drawn nodes, and a state the client can send back unchanged."""
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    sc = scene(m, SceneState(budget=12))
    ids = [n["id"] for n in sc["nodes"]]
    assert len(ids) == len(set(ids))
    seen: set[str] = set()
    for n in sc["nodes"]:
        assert n["parent"] is None or n["parent"] in seen
        seen.add(n["id"])
    assert all(e["src"] in seen and e["dst"] in seen for e in sc["edges"])
    assert len({e["key"] for e in sc["edges"]}) == len(sc["edges"])
    assert any(e["style"] == "http" for e in sc["edges"])
    again = scene(m, SceneState(expanded=sc["state"]["expanded"], budget=12))
    assert again["nodes"] == sc["nodes"] and again["edges"] == sc["edges"]


def test_folding_drops_what_was_unfolded_inside(repo: Path):
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    inner = SceneState(expanded=["fs:.", "fs:backend", "fs:backend/store.py"])
    assert "fs:backend/store.py" in scene(m, inner)["state"]["expanded"]
    folded = scene(m, SceneState(expanded=["fs:.", "fs:backend/store.py"]))
    assert folded["state"]["expanded"] == ["fs:."]


def test_hover_descriptions(repo: Path):
    """Every drawn box and edge describes itself in plain sentences, with the data set apart."""
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    text = lambda line: line.replace("`", "")  # noqa: E731
    sc = scene(m, SceneState(budget=12))
    for item in sc["nodes"] + sc["edges"]:
        assert item["info"], item.get("id") or item.get("key")
        assert isinstance(item["info"], str)
        assert not any(t in text(item["info"]) for t in ("fs:", "py:", "svc ", "img ")), item["info"]
    http = next(e for e in sc["edges"] if e["style"] == "http")
    assert "to endpoints served by" in text(http["info"])
    # with the APIs group hidden, calls are in the caller's hover text, not edges
    folded = scene(m, SceneState(budget=12, groups=[]))
    assert not any(e["style"] == "http" for e in folded["edges"])
    app = next(n for n in folded["nodes"] if n["id"] == "fs:frontend/src/App.tsx")
    assert "Calls 1 HTTP endpoint" in text(app["info"]).split("\n\n")
    # a Python reference edge names what it uses ("api/ references Store in store")
    imp = next(e for e in sc["edges"] if e["style"] == "import" and " references " in text(e["info"]))
    assert "`Store`" in imp["info"], imp["info"]


def test_groups_are_a_filter(repo: Path):
    """A shown group is a box like any other (edges attach to it, it unfolds); a group the
    filter hides is not drawn, its edges are badges, and it can't be unfolded."""
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    shown = scene(m, SceneState(budget=12, groups=["api"]))
    apis = next(n for n in shown["nodes"] if n["id"] == "api")
    assert apis["foldable"] and not apis["container"]
    assert any("api" in (e["src"], e["dst"]) for e in shown["edges"])
    assert {g["id"]: g["shown"] for g in shown["groups"]}["api"] is True

    opened = scene(m, SceneState(expanded=shown["state"]["expanded"] + ["api"], groups=["api"]))
    assert any(n["parent"] == "api" for n in opened["nodes"])

    hidden = scene(m, SceneState(expanded=shown["state"]["expanded"] + ["api"], groups=[]))
    assert "api" not in hidden["state"]["expanded"]
    assert all(n["id"] != "api" and n["parent"] != "api" for n in hidden["nodes"])
    assert not any(n["id"] in ("env", "deploy") for n in hidden["nodes"])  # env set by deployment: both hidden, no edge


def test_utilities_by_incoming_edges(repo: Path):
    """A box with more than N incoming dependency edges is a utility: its incoming edges
    become badges on their sources. Boxes in groups never are."""
    from nolli.model import USE
    from nolli.rollup import utility_badges
    from nolli.scene import SceneState, compute

    m, _ = extract(repo)
    _x, _h, _v, rolled, _u = compute(m, SceneState(utilities=0))
    before = list(rolled.edges)
    utilities = utility_badges(m, rolled, min_in=0)  # every used box outside a group
    targets = {e.dst for e in before if e.kind == USE}
    assert utilities == {t for t in targets if t != "api"}
    assert not any(e.kind == USE and e.dst in utilities for e in rolled.edges)
    src = next(e.src for e in before if e.kind == USE and e.dst in utilities)
    assert any(k.startswith("util:") for k in rolled.annotations[src])
    _x, _h, _v, rolled, _u = compute(m, SceneState(utilities=0))
    assert utility_badges(m, rolled, min_in=1) == set()  # nothing has more than one


def test_folder_text_matches_unfolding(repo: Path):
    """A folder's sentence counts exactly the boxes that unfolding then draws, and modules
    report their lines (from the Python front-end's describe module)."""
    import re
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    text = lambda line: line.replace("`", "")  # noqa: E731
    before = scene(m, SceneState(expanded=["fs:."]))
    backend = next(n for n in before["nodes"] if n["id"] == "fs:backend")
    said = text(backend["info"].split("\n\n")[0])
    assert said.startswith("backend/ is a Python package with "), said
    promised = sum(int(n) for n in re.findall(r"(\d+) ", said))
    after = scene(m, SceneState(expanded=["fs:.", "fs:backend"]))
    drawn = [n for n in after["nodes"] if n["parent"] == "fs:backend" and n["kind"] != "anchor"]
    assert promised == len(drawn), (said, [n["id"] for n in drawn])
    store = next(n for n in after["nodes"] if n["id"] == "fs:backend/store.py")
    assert re.search(r"is a Python module with \d+ lines? and 1 class", text(store["info"])), store["info"]
    assert not any("pkgs" in (n.get("badge") or "") for n in after["nodes"])  # dependencies are in the text, not on the box


def test_docstrings(tmp_path: Path):
    """Module, class and package docstrings are read from the syntax tree, not imported,
    and shown as their own line."""
    from nolli.scene import SceneState, scene

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text('"""Tools for frobbing.\n\nLong details nobody reads."""\n')
    (tmp_path / "pkg/core.py").write_text('"""The core frobber."""\nimport sys\nsys.exit("must not run")\n\n'
                                          'class Frob:\n    """Frobs one thing at a time."""\n')
    m, _ = extract(tmp_path)
    assert m.entities["fs:pkg/core.py"].attrs["doc"] == "The core frobber."
    assert m.entities["py:pkg/core.py#Frob"].attrs["doc"] == "Frobs one thing at a time."
    sc = scene(m, SceneState(expanded=["fs:.", "fs:pkg", "fs:pkg/core.py"]))
    docs = {n["id"]: [p[2:].replace("\\", "") for p in n["info"].split("\n\n") if p.startswith("> ")] for n in sc["nodes"]}
    assert docs["fs:pkg"] == ["Tools for frobbing."]  # first paragraph only
    assert docs["py:pkg/core.py#Frob"] == ["Frobs one thing at a time."]


def test_own_code_dot(tmp_path: Path):
    """A module's own-code dot names its functions (most referenced first) and constants;
    a package's names what its __init__.py defines and re-exports."""
    from nolli.scene import SceneState, scene

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("from .core import Frob, frob\n\ndef setup():\n    return Frob()\n")
    (tmp_path / "pkg/core.py").write_text("LIMIT = 3\n\ndef helper():\n    pass\n\ndef frob():\n    return Frob()\n\n"
                                          "class Frob:\n    def go(self):\n        return frob()\n")
    (tmp_path / "pkg/use.py").write_text("from .core import frob\nfrob(); frob()\n")
    m, _ = extract(tmp_path)
    core = m.entities["fs:pkg/core.py"].attrs
    assert core["functions"] == ["helper", "frob"] and core["constants"] == ["LIMIT"] and core["own_lines"] == 8
    assert m.entities["fs:pkg/__init__.py"].attrs["reexports"] == ["Frob", "frob"]
    sc = scene(m, SceneState(expanded=["fs:.", "fs:pkg", "fs:pkg/core.py"]))
    text = lambda line: line.replace("`", "")  # noqa: E731
    info = {n["id"]: text(n["info"]).split("\n\n") for n in sc["nodes"]}
    assert info["body:fs:pkg/core.py"][0] == "core defines frob and helper"  # frob is referenced, helper not
    assert "the constant LIMIT" in info["body:fs:pkg/core.py"][1]
    assert any("re-exports Frob and frob" in t for t in info.get("body:fs:pkg", [])) or "body:fs:pkg" not in info


def test_directory_with_source_inside_stays_shown():
    """A directory whose bytes are mostly config is still drawn when it holds source files."""
    from nolli.model import Entity, Model, Provenance
    from nolli.rollup import hidden_by_role

    m = Model()
    for eid, kind, role in [("fs:.", "directory", "mixed"), ("fs:evals", "directory", "config"), ("fs:evals/probe.py", "file", "source"),
                            ("fs:evals/runs.json", "file", "config"), ("fs:docs", "directory", "docs"), ("fs:docs/a.md", "file", "docs")]:
        m.add_entity(Entity(eid, kind, eid.rsplit("/", 1)[-1], {"role": role, **({"root": True} if eid == "fs:." else {})}, Provenance("t")))
    for parent, child in [("fs:.", "fs:evals"), ("fs:evals", "fs:evals/probe.py"), ("fs:evals", "fs:evals/runs.json"), ("fs:.", "fs:docs"), ("fs:docs", "fs:docs/a.md")]:
        m.contain(parent, child, "t")
    assert hidden_by_role(m) == {"fs:evals/runs.json", "fs:docs", "fs:docs/a.md"}


def test_box_names_are_icons_then_a_name(repo: Path):
    """Every box: small icons saying what it is, then a readable name."""
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    app = "api:http:backend/main.py:app"
    worlds = app + "/api/worlds"
    sc = scene(m, SceneState(expanded=["fs:.", "fs:backend", "api", app, worlds], groups=["api"]))
    names = {n["id"]: (n["icons"], n["label"]) for n in sc["nodes"] if "icons" in n}
    assert names["fs:backend/store.py"] == (["m"], "store")
    assert names[app] == (["API"], "backend")
    assert names[worlds] == (["/"], "api/worlds")
    assert names[app + " POST /api/worlds"] == (["POST"], "")
    assert names[app + " GET /api/worlds/{world}/echoes"] == (["GET"], "{world}/echoes")


def test_markdown_from_repository_text():
    """Names and docstrings from a repository can't turn into Markdown (prose.py)."""
    from nolli.prose import code, esc, quote, say

    assert say("{} references {} in {}", "api/", "1 name", "lib/") == "`api/` references `1 name` in `lib/`"
    assert code("a`b") == "``a`b``" and code("`x`") == "`` `x` ``"
    assert esc("__init__.py *[x](y)*") == r"\_\_init\_\_.py \*\[x\](y)\*"
    assert quote("The core\n  frobber.") == "> The core frobber."
    # a docstring's own code spans stay, backslashes and all; the rest is text
    assert quote("Used by ``nolli/cli.py`` and `a_b`, not *this* or ` alone") == r"> Used by ``nolli/cli.py`` and `a_b`, not \*this\* or \` alone"


def test_mermaid_draws_the_scene(repo: Path):
    """The Mermaid renderer draws the scene it is given, for any scene state: every box (an
    unfolded one as a subgraph), every edge, and nothing else; the repository root is the
    canvas, not a subgraph."""
    import re

    from nolli.mermaid import nid, render
    from nolli.scene import SceneState, scene

    m, _ = extract(repo)
    for st in (SceneState(budget=12), SceneState(preset="folded"), SceneState(preset="unfolded"),
               SceneState(expanded=["fs:.", "fs:backend"])):
        sc = scene(m, st)
        text = render(sc)
        boxes = {g.group(1) for line in text.splitlines() if (g := re.match(r"\s*(?:subgraph )?(n_\w+?)(?:_g)? ?[\[(]", line))}
        assert boxes == {nid(n["id"]) for n in sc["nodes"]}
        edges = [line for line in text.splitlines() if re.match(r"\s*n_\w+ (?:-->|-\.->|==>)", line)]
        assert len(edges) == len(sc["edges"])
        assert nid("fs:.") not in text


def test_presets(repo: Path):
    """A preset names a cut; the scene says which one gave it, and none once boxes are given."""
    from nolli.scene import SceneState, scene, state_from_query

    m, _ = extract(repo)
    folded = scene(m, state_from_query({"p": "folded"}))
    assert folded["state"]["expanded"] == ["fs:."] and folded["state"]["preset"] == "folded"
    unfolded = scene(m, SceneState(preset="unfolded"))
    assert {"fs:backend", "fs:backend/api"} <= set(unfolded["state"]["expanded"])
    assert scene(m, SceneState(preset="changes"))["state"]["preset"] == "default"  # not a change's dataset
    assert scene(m, state_from_query({"p": "folded", "x": "fs:.,fs:backend"}))["state"]["preset"] is None
