"""TypeScript and JavaScript on a synthetic repository: classes and types as entities,
references through barrels and namespaces, type-only dependencies, and the resolution
particulars (NodeNext ``.js`` specifiers, tsconfig ``extends``/``paths``/``baseUrl``,
workspace packages, CommonJS)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nolli.cli import extract
from nolli.info import edge_info
from nolli.labels import module_stem
from nolli.prose import Ctx
from nolli.rollup import RolledEdge, is_init

FILES = {
    "tsconfig.base.json": '{ // shared\n "compilerOptions": { "baseUrl": "./app/src", "paths": { "@/*": ["./*"] } } }\n',
    "app/tsconfig.json": '{ "extends": "../tsconfig.base.json", "compilerOptions": { "strict": true, } }\n',
    "app/src/model/task.ts": (
        "/** A task as the API returns it. */\n"
        "export interface Task { id: TaskId; title: string }\n"
        "export type TaskId = string\n"
        "export const enum Priority { Low, High }\n"
        "interface Task { done?: boolean }\n"  # declaration merging: one entity
    ),
    "app/src/model/index.ts": "export * from './task.js'\nexport { Store as TaskStore } from '../store/store.js'\n",
    "app/src/store/store.ts": (
        "import type { Task } from '../model/index.js'\n"
        "import { Priority } from '@/model/index.js'\n"
        "import * as fmt from 'lib/format'\n"
        "export default class Store {\n  items: Task[] = []\n  top() { return fmt.title(this.items[0], Priority.High) }\n}\n"
        "export const MAX_ITEMS = 100\n"
    ),
    "app/src/lib/format.ts": (
        "import { Task } from '../model/index.js'\n"
        "export function title(t: Task, p: number) { return t.title + p }\n"
    ),
    "app/src/commands/list.ts": (
        "import Store from '../store/store.js'\nimport { TaskStore } from '../model/index.js'\n"
        "import { shout } from '@acme/util'\nimport { render } from '@acme/util/render'\n"
        "export function list() { return shout(render(new Store(), TaskStore)) }\n"
        "export const later = async () => (await import('./later.js')).run\n"
    ),
    "app/src/commands/later.ts": "export function run() {}\n",
    "app/src/main.tsx": "import { list } from './commands/list.js'\nimport React from 'react'\n"
    "import { marked } from 'https://cdn.jsdelivr.net/npm/marked@12/lib/marked.esm.js'\nexport function App() { return <div>{list()}</div> }\n",
    "packages/util/package.json": '{ "name": "@acme/util", "main": "dist/index.js" }\n',
    "packages/util/src/index.ts": "export function shout(s: unknown) { return String(s).toUpperCase() }\n",
    "packages/util/src/render.ts": "export function render(...a: unknown[]) { return a.join() }\n",
    "package.json": '{ "name": "acme", "workspaces": ["app", "packages/*"] }\n',
    "legacy/server.js": "const { start } = require('./http')\nconst db = require('./db')\nclass Server { run() { start(db.connect()) } }\nmodule.exports = { Server }\n",
    "legacy/http.js": "function start() {}\nmodule.exports = { start }\n",
    "legacy/db.js": "exports.connect = () => 1\n",
}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("ts")
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    m, ctx = extract(root)
    return m, ctx


def uses(m, subkind=None):
    return {(r.src, r.dst): r for r in m.relations if r.kind == "use" and (subkind is None or r.subkind == subkind)}


def refs_between(m, src, dst):
    return [r for r in m.relations if r.kind == "use" and r.subkind == "ref" and (r.src, r.dst) == (src, dst)]


def test_classes_and_types_are_entities(model):
    m, _ = model
    kinds = {e.id: e.kind for e in m.entities.values() if e.id.startswith("ts:")}
    assert kinds == {
        "ts:app/src/model/task.ts#Task": "interface",
        "ts:app/src/model/task.ts#TaskId": "type",
        "ts:app/src/model/task.ts#Priority": "enum",
        "ts:app/src/store/store.ts#Store": "class",
        "ts:legacy/server.js#Server": "class",
    }, "one entity per name (merged declarations), functions are not entities"
    task = m.entities["ts:app/src/model/task.ts#Task"]
    assert m.parent(task.id) == "fs:app/src/model/task.ts"
    assert task.attrs["doc"] == "A task as the API returns it." and task.attrs["exported"]
    assert m.entities["ts:legacy/server.js#Server"].attrs["language"] == "javascript"
    store = m.entities["fs:app/src/store/store.ts"].attrs
    assert store["constants"] == ["MAX_ITEMS"]
    assert m.entities["fs:app/src/commands/list.ts"].attrs["functions"] == ["list", "later"]


def test_references_follow_barrels_and_namespaces(model):
    m, _ = model
    refs = uses(m, "ref")
    # a type through ``export * from`` in the barrel, a value through tsconfig ``paths``
    assert ("ts:app/src/store/store.ts#Store", "ts:app/src/model/task.ts#Task") in refs
    assert ("ts:app/src/store/store.ts#Store", "ts:app/src/model/task.ts#Priority") in refs
    # ``fmt.title`` through ``import * as fmt`` from a ``baseUrl`` path: the module that defines it
    assert ("ts:app/src/store/store.ts#Store", "fs:app/src/lib/format.ts") in refs
    # a default import, and a renamed re-export (``Store as TaskStore``) to the class itself
    assert {r.attrs["name"] for r in refs_between(m, "fs:app/src/commands/list.ts", "ts:app/src/store/store.ts#Store")} \
        == {"Store", "TaskStore"}
    # a type referencing a type of its own module
    assert ("ts:app/src/model/task.ts#Task", "ts:app/src/model/task.ts#TaskId") in refs
    # the import edges are replaced where references cover them
    assert ("fs:app/src/commands/list.ts", "fs:app/src/store/store.ts") not in uses(m, "import")


def test_type_only_dependencies(model):
    m, _ = model
    refs = uses(m, "ref")
    assert refs[("ts:app/src/store/store.ts#Store", "ts:app/src/model/task.ts#Task")].attrs["type_only"], "import type"
    assert refs[("fs:app/src/lib/format.ts", "ts:app/src/model/task.ts#Task")].attrs["type_only"], "used in type positions only"
    assert not refs[("ts:app/src/store/store.ts#Store", "ts:app/src/model/task.ts#Priority")].attrs["type_only"]
    ctx = Ctx(m, None, set(), set())
    e = RolledEdge("use", "fs:app/src/lib/format.ts", "ts:app/src/model/task.ts#Task", "import", 1, types=1)
    assert "no dependency at run time" in str(edge_info(ctx, e))


def test_resolution_particulars(model):
    m, ctx = model
    imports = uses(m, "import")
    # workspace packages resolve to their source, the entry from ``main`` mapped dist/ -> src/
    assert ("fs:app/src/commands/list.ts", "fs:packages/util/src/index.ts") in uses(m)
    assert ("fs:app/src/commands/list.ts", "fs:packages/util/src/render.ts") in uses(m)
    assert not m.has("ext:js:@acme/util")
    assert ("fs:app/src/main.tsx", "ext:js:react") in imports
    assert ("fs:app/src/main.tsx", "ext:js:marked") in imports, "a CDN URL names its package"
    # a dynamic import() stays an import edge, marked
    assert imports[("fs:app/src/commands/list.ts", "fs:app/src/commands/later.ts")].attrs["dynamic"]
    # CommonJS: destructured and namespace require
    refs = uses(m, "ref")
    assert ("ts:legacy/server.js#Server", "fs:legacy/http.js") in refs
    assert ("ts:legacy/server.js#Server", "fs:legacy/db.js") in refs
    assert ctx.notes["js-resolution"].get("unresolved", 0) == 0


def test_declaration_files_are_named_as_modules():
    assert module_stem("vite-env.d.ts") == "vite-env" and module_stem("api.ts") == "api" and module_stem("a.b.js") == "a.b"


def test_index_is_the_folders_own_code(model):
    m, _ = model
    assert is_init(m, "fs:app/src/model/index.ts")
    assert m.entities["fs:app/src/model/index.ts"].attrs["reexports"] == ["TaskStore"]
