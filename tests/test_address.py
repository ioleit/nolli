"""The model's namespaces (nolli/model/address.py): addresses are made and read only there."""

import re
from pathlib import Path

import pytest

from nolli.model import CONTAIN, ENTITY_KINDS, USE, Entity, Model, Relation, address

ROOT = Path(__file__).resolve().parent.parent
OLD = ("fs", "py", "ts", "svc", "img", "host", "env", "ext", "body")  # the prefixes before the namespaces
SCHEMES = "|".join((*address.NAMESPACES, *OLD))
SPELLED = re.compile(rf"""(?<![\w.])[fr]?["'](?:{SCHEMES}):""")


def test_no_module_spells_a_scheme():
    """A scheme in a string literal outside address.py means an id built or taken apart by
    hand: use address.py's functions, so the namespaces stay the one place that defines them."""
    found = []
    for p in sorted((ROOT / "nolli").rglob("*.py")):
        if p.name == "address.py":
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if SPELLED.search(line):
                found.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    for p in sorted((ROOT / "viewer" / "js").glob("*.js")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if SPELLED.search(line) and "REPO_ROOT =" not in line:
                found.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not found, "\n".join(found)


def test_kinds_are_the_models():
    kinds = {k for ns in address.NAMESPACES.values() for k in ns.kinds}
    assert kinds | {"group"} == set(ENTITY_KINDS)


def test_repo_addresses():
    assert address.repo(".") == address.repo("") == address.REPO_ROOT == "repo:"
    f = address.repo("nolli/scene.py")
    assert f == "repo:nolli/scene.py" and address.path_of(f) == "nolli/scene.py" and address.path_of("repo:") == "."
    c = address.part(f, "Outer", "Inner")
    assert c == "repo:nolli/scene.py#Outer.Inner" == address.repo("nolli/scene.py", "Outer", "Inner")
    assert address.fragment_of(c) == ("Outer", "Inner") and address.file_of(c) == f and address.path_of(c) == "nolli/scene.py"
    assert address.is_part(c) and not address.is_part(f)
    odd = address.part(f, "a.b#c")  # a segment holding the separators is escaped
    assert address.fragment_of(odd) == ("a.b#c",) and odd.count("#") == 1


def test_own_code():
    f = address.repo("pkg/core.py")
    own = address.own_code(f)
    assert own == "repo:pkg/core.py#" and address.is_own_code(own) and not address.is_part(own)
    assert address.owner(own) == f and address.owner(f) == f and address.own_code(address.part(f, "X")) == own


def test_other_namespaces():
    app = address.api_app("http", "server/main.py:app")
    assert address.api_operation(app, ["items", "{item_id}"], "GET") == "api:http:server/main.py:app/items/{}#GET"
    assert address.api_operation(app, ["items", "{id}"], "GET") == address.api_operation(app, ["items", "{item_id}"], "GET")
    assert address.package("js", "elkjs") == "pkg:npm/elkjs"
    assert address.package("js", "@scope/name") == "pkg:npm/%40scope/name"
    assert address.package("py", "yaml") == "pkg:python/yaml"
    assert address.built("web/Dockerfile") == "pkg:build/web/Dockerfile"
    assert address.runtime("db") == "runtime:db" and address.config("HOME") == "config:HOME"
    u = address.unresolved("py", "a.b")
    assert address.unresolved_key(u, "py") == "a.b" and address.unresolved_key(u, "js") is None
    assert {address.scheme_of(x) for x in (app, u, "pkg:", "repo:")} == {"api", "unresolved", "pkg", "repo"}
    with pytest.raises(ValueError):
        address.scheme_of("fs:nolli")


def test_the_model_keeps_to_the_namespaces():
    m = Model()
    m.add_entity(Entity("repo:", "directory", "r"))
    m.add_entity(Entity("repo:a.py", "file", "a.py"))
    with pytest.raises(ValueError, match="no place"):
        m.add_entity(Entity("repo:b", "service", "b"))
    with pytest.raises(ValueError, match="not an address"):
        m.add_entity(Entity("fs:b", "file", "b"))
    with pytest.raises(ValueError, match="is a file"):
        m.add_entity(Entity("repo:a.py", "directory", "a.py"))
    db = m.add_entity(Entity(address.runtime("db"), "service", "db")).id
    root = m.ensure_root(address.RUNTIME, "test")
    assert m.entities[root].kind == "group" and m.entities[root].name == "Runtime"
    m.add_relation(Relation(CONTAIN, root, db))
    with pytest.raises(ValueError, match="crosses"):
        m.add_relation(Relation(CONTAIN, "repo:", db))
    with pytest.raises(ValueError, match="no use from runtime: to repo:"):
        m.add_relation(Relation(USE, db, "repo:a.py"))
