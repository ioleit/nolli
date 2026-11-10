"""The schemes and the addresses in them (``EntityId``, nolli/model/schema.py): addresses are
made and read only there."""

import re
from pathlib import Path

import pytest

from nolli.logic.extractors.facts import EntityFact, Facts, RelationFact
from nolli.model.model import Entity, Model, Relation
from nolli.model.schema import EntityId, EntityKind, RelationKind, Scheme

ROOT = Path(__file__).resolve().parent.parent
OLD = ("fs", "py", "ts", "svc", "img", "host", "env", "ext", "body")  # the prefixes before the namespaces
SCHEMES = "|".join((*Scheme, *OLD))
SPELLED = re.compile(rf"""(?<![\w.])[fr]?["'](?:{SCHEMES}):""")


def test_no_module_spells_a_scheme():
    """A scheme in a string literal outside schema.py means an id built or taken apart by
    hand: use ``EntityId``'s methods, so the namespaces stay the one place that defines them."""
    found = []
    for p in sorted([*(ROOT / "nolli").rglob("*.py"), *(ROOT / "viewer").glob("*.py")]):
        if p.relative_to(ROOT).as_posix() == "nolli/model/schema.py":
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if SPELLED.search(line):
                found.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    for p in sorted((ROOT / "viewer" / "js").glob("*.js")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if SPELLED.search(line) and "REPO_ROOT =" not in line:
                found.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not found, "\n".join(found)


def test_repo_addresses():
    assert EntityId.from_path(".") == EntityId.from_path("") == EntityId.REPO_ROOT == "repo:"
    f = EntityId.from_path("nolli/scene.py")
    assert f == "repo:nolli/scene.py" and f.path == "nolli/scene.py" and EntityId("repo:").path == "."
    c = f.part("Outer", "Inner")
    assert c == "repo:nolli/scene.py#Outer.Inner" == EntityId.from_path("nolli/scene.py", "Outer", "Inner")
    assert c.fragment == ("Outer", "Inner") and c.file == f and c.path == "nolli/scene.py"
    assert c.is_part and not f.is_part
    odd = f.part("a.b#c")  # a segment holding the separators is escaped
    assert odd.fragment == ("a.b#c",) and odd.count("#") == 1


def test_own_code():
    f = EntityId.from_path("pkg/core.py")
    own = f.own_code()
    assert own == "repo:pkg/core.py#" and own.is_own_code and not own.is_part
    assert own.owner == f and f.owner == f and f.part("X").own_code() == own


def test_other_namespaces():
    app = EntityId.from_api_app("http", "server/main.py:app")
    assert app.api_operation(["items", "{item_id}"], "GET") == "api:http:server/main.py:app/items/{}#GET"
    assert app.api_operation(["items", "{id}"], "GET") == app.api_operation(["items", "{item_id}"], "GET")
    assert EntityId.from_package("js", "elkjs") == "pkg:npm/elkjs"
    assert EntityId.from_package("js", "@scope/name") == "pkg:npm/%40scope/name"
    assert EntityId.from_package("py", "yaml") == "pkg:python/yaml"
    assert EntityId.from_build_recipe("web/Dockerfile") == "pkg:build/web/Dockerfile"
    assert EntityId.from_host("db") == "runtime:db" and EntityId.from_config("HOME") == "config:HOME"
    u = EntityId.from_unresolved("py", "a.b")
    assert u.unresolved_key("py") == "a.b" and u.unresolved_key("js") is None
    assert {EntityId(x).scheme for x in (app, u, "pkg:", "repo:")} == {"api", "unresolved", "pkg", "repo"}
    with pytest.raises(ValueError):
        EntityId("fs:nolli")


def test_what_extraction_and_a_model_check():
    """Extraction (``Facts``) checks addresses and kinds as it grows, and one kind per address;
    a ``Model`` that every edge's ends are its entities and that a node has one owner."""
    m = Facts()
    m.add_entity(EntityFact("repo:", "directory", "r"))
    m.add_entity(EntityFact("repo:a.py", "file", "a.py"))
    with pytest.raises(ValueError, match="not an address"):
        m.add_entity(EntityFact("fs:b", "file", "b"))
    with pytest.raises(ValueError, match="EntityKind"):
        m.add_entity(EntityFact("repo:b", "fiel", "b"))
    with pytest.raises(ValueError, match="is a file"):
        m.add_entity(EntityFact("repo:a.py", "directory", "a.py"))
    db = m.add_entity(EntityFact(EntityId.from_host("db"), "service", "db")).id
    root = m.ensure_root(Scheme.RUNTIME, "test")
    assert m.entities[root].kind == EntityKind.GROUP and m.entities[root].name == "Runtime"
    m.add_relation(RelationFact(RelationKind.CONTAIN, root, db))
    with pytest.raises(ValueError, match="RelationKind"):
        m.add_relation(RelationFact("uses", db, "repo:a.py"))
    with pytest.raises(KeyError, match="unknown entity"):
        m.add_relation(RelationFact(RelationKind.USE, db, "repo:gone.py"))
    model, _ = m.split()
    assert model.entities[root].kind == "group" and model.find_parent(db) == root
    with pytest.raises(ValueError, match="unknown entity kind"):
        Entity("repo:b", "fiel")
    with pytest.raises(KeyError, match="unknown node"):
        Model(entities=[Entity("repo:", "directory")], edges=[Relation(RelationKind.CONTAIN, "repo:", "repo:b")])
    with pytest.raises(ValueError, match="two owners"):
        Model(entities=[Entity("repo:", "directory"), Entity("repo:a", "directory"), Entity("repo:a/b", "file")],
              edges=[Relation(RelationKind.CONTAIN, "repo:", "repo:a/b"), Relation(RelationKind.CONTAIN, "repo:a", "repo:a/b")])
