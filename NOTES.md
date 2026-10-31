# Notes: judgement calls so far

Everything here is provisional. Each entry is a choice made while building the prototype
on a few repositories, with the reason, so a later change knows what it is undoing.
Nothing is a settled design.

## Model

- Vocabulary from UML 2 component/deployment diagrams: artifacts (directory, file),
  classes, components (service, image), interfaces (endpoint, env, host), external
  packages; relations `contain` (one ownership tree), `use`, `provide` (realization),
  `allocate` (software element → environment element). Chosen because it exists and
  covers the cases; not derived from requirements.
- Facts at the finest resolved level, never pre-aggregated. Coarser edges come from the
  roll-up at view time (`nolli/rollup.md`). This is what makes the depth adjustable.
- Classes are the finest atom; functions and module-level code are the module's own body.
  The picture is about architecture, not call graphs. In TypeScript the atoms are
  classes, interfaces, type aliases and enums: interfaces and types are what Python's data
  classes are, the shapes modules agree on, and real TypeScript has few classes (7 in a
  265-module CLI next to 182 interfaces and types; 1 in a 98-module React frontend next
  to 118).
  React components stay functions of their module: in the frontend looked at, a file
  holds one component, so the module already is the component. Open for other
  languages: the "smallest unit with state and an interface" (struct+impl in Rust?
  package-level types in Go?).
- A folder's package body (Python's `__init__.py`, an `index.ts`/`index.js`) is the
  folder's own code, not a module of its own; each language names its body files.
- A TypeScript reference used only as a type (or imported with `import type`) is marked
  `type_only`: TypeScript erases it, so it is no dependency at run time and a cycle
  through it is none either. A drawn edge made only of such references is dotted and
  says so; a mixed edge says how many are types.
- References replace imports where they resolve: a `use/ref` edge runs from the class or
  module whose body mentions a name to the class or module it is bound to, following
  re-exports; where references between two modules exist the import edge is dropped. A
  count therefore means references, not import statements.
- Roles (source, test, config, docs, markup, data, asset) instead of ignore lists; views
  hide roles and report how much they hid. A directory is source if source is at least a
  tenth of its bytes; dot-directories are config; HTML/CSS are "markup" and do not make a
  directory source (this hides a directory whose logic lives in HTML; see backlog).
- Unresolved references are entities under one group, counted in the residue, never
  silently dropped. Standard library and node built-ins are not dependencies.

## Extraction

- Two layers that know nothing of each other's code: extraction (`extractors/`, `match`)
  writes the graph; the operations on it (roll-up, labels, hover text, scene, Mermaid,
  diff) read only the graph. What they need to know about a language is a fact in the
  model, written by the file tree: a file is a `module`, a package body is `body` (what
  it makes its folder: "package", "folder module"), and `meta["languages"]` names the
  languages. Symbol kinds are the model's vocabulary (`model.SYMBOL_KINDS`), so their
  icons and nouns live with the picture. A dataset therefore describes itself; one
  extracted before these facts existed draws package bodies as modules.
- A language is a plug-in (`extractors/<language>/__init__.py` exports a `Language`):
  extensions, parser, extractors, resolvers, its name in text, package-body files.
  Only extraction reads the registry.
  Extractors of a language share one parse per file where trees are cheap to keep
  (tree-sitter); Python's `ast` trees are not kept: for a 2,000-file repository they
  took 700 MB and the garbage collector walking them cost what parsing once saved.
- Python modules resolve relative to any ancestor directory of the importing file and
  to `src/`, `lib/`, `python/` roots; FastAPI `include_router` prefixes are followed
  through re-exports.
- TypeScript/JavaScript specifiers resolve the way the compiler does where it matters
  in practice: `./x.js` finds `x.ts` (NodeNext), a folder its `index`, tsconfig `paths`
  and `baseUrl` with `extends` followed, packages of the repository's own workspaces to
  their source (an entry under `dist/` is looked for under `src/`), CDN URLs to their npm
  package. References follow `export … from` and `export *` through barrels to the
  class or type, else to the module defining the name; namespace imports (`ns.Name`)
  and CommonJS `require` (whole or destructured) bind like imports. `import()` stays an
  import edge, marked `dynamic`.
- Frontend calls: `fetch`/axios/EventSource with their URL's static shape; one level of
  helper indirection (`fetchJSON(path)`), with module constants (`API_BASE`) folded in;
  a route whose path ends with the called pattern is accepted when the base is unknown.
  Marked heuristic; drawn paler.
- Compose: `depends_on` and URLs in env values are `use` between services; env values
  whose key looks like an allowed-origins list (`CORS`, `ORIGIN`, `ALLOWED`, …) are not
  dependencies. Every env variable a service sets is `provide(service, env)`, so a code
  read meets the service that sets it.
- Dockerfile `COPY` is `allocate(directory, image)`; `CMD`/`ENTRYPOINT` naming a module
  is `use(image, module)`.

- APIs are one structure for every framework and protocol (`extractors/api.py`): adapters
  only report operations (protocol, which API they belong to, verb, path, handler file)
  and calls; one builder makes APIs → one box per served API (a FastAPI app; a router
  nobody mounts is its own, heuristic) → its path as a tree → operations. Path steps
  without operations of their own are merged (`conversations/{project_name}`), a step with
  one operation is that operation (`GET health`), an operation inside a step is labelled
  by its verb; parameters at one position are one step whatever their names. Protocol
  knowledge left: splitting a path, and the matcher (HTTP path patterns; exact names for
  anything else so far).

## Picture

- Default cut: unfold the box that reveals the most edges per node it adds (edges whose
  lowest common ancestor is the box), until a budget (34) is reached. Groups with no
  internal structure never unfold by themselves.
- Unfolding shows all children. A partially opened box hides problems. Only the − folds.
- Set-but-unread env variables are hidden; endpoints nobody in the repo calls are shown
  (they are the service's surface).
- Allocation (what is copied into which service) is drawn as coloured chips on the
  code box, not edges. Env variables are edges only when the Environment group is shown
  (reader → variable → service that sets it); otherwise badges. Both are open for
  different rendering (backlog).
- Numbers that are guesses: budget 34, utility threshold more than 4 incoming edges, weak
  edge under 5% and fewer than 3 references, fan bundling from 3 children.

## Viewer and server

- One implementation of the roll-up, in Python. The viewer sends its state (unfolded
  boxes, roles, options) to `/api/scene` and draws the answer; it computes no edges,
  labels or badges. Cost: one request per click, 10–60 ms on the corpus, ~200 ms when
  the server has to compute a default cut on the largest repository.
- The server normalises the unfolded set to the containers actually reached, so folding a
  box drops whatever was unfolded inside it, and the client adopts that set.
- The scene is flat (nodes in drawing order, each naming its drawn parent) rather than
  nested, so two scenes can be diffed by id; edges are keyed `kind|src|dst|subkind`.
- Moving the roll-up out of the browser found one drift: the browser cut the `body:`
  prefix with the length of the older `files:` prefix, so a module's own-code anchor had
  no drawn parent there. Edges into it were never bundled or marked internal and it
  could become a nameless "utility". The Python rules were right; default pictures
  with such anchors change accordingly.

## Diff (first version)

- Compare the facts, not the pictures: both commits are extracted and merged into one
  model, and the picture is rolled up from that. A change inside a folded box is then
  still visible at any depth. Comparing two rendered scenes would lose it.
- Each side comes from `git archive`, so the repository is never checked out or modified.
- Identity across commits is the path-based id. A rename or move is therefore one entity
  removed and one added; the file records link the two (`old_path`, `renamed_to`), but
  nothing merges them yet.
- Two layers of change: structural (entities and relations present on one side only,
  or with a different reference count) and content (changed lines per file and per
  class, from `git diff -M -U0`). Content is recorded even when nothing structural
  changed, so a review can still point at it. Classes record their end line for this.
- Records sit in `attrs["diff"]` only on what changed; base and head commits and the
  GitHub remote are in `meta["diff"]`, enough to build links later.

## Review mode (first version)

- The change rolls up with the picture: a drawn edge knows its references before and
  after and the facts that changed under it, also after bundling; a box knows the
  changed lines, files and entities inside it. Status follows: added, removed, changed.
- Changes are numbered per dataset in a fixed order (components, dependencies between
  modules, edited files; source code before tests, docs, config, data), for the list and
  for links (`item=C3`). A dependency item's status covers all references between the two modules,
  not only the changed ones: a module that already used another and gains one more
  reference is "changed", not "new".
- Every edited file is an item, structural change or not, so it can be found in the list.
- The review agent sees what a reviewer sees: one diagram with every area that changed in
  source code unfolded (what going to each change unfolds, together). It answers on the
  diagram's own node and edge ids, not on the numbered changes: an earlier brief listed
  the numbered changes next to the diagram, and the agent reviewed the list instead of
  the picture and cited ids that mean nothing to someone looking at the drawing.
- A verdict maps back through the Mermaid id to an entity (or two, for an edge), so it
  stays on whatever box or edge stands for it, at any depth. Only question and problem are
  marked in the drawing.
- The brief asks for the diagram first (how expected is this, given the title; what does
  it do to the graph: cycles, layer direction, hubs, boundaries), then the description and
  the code only for the reasons; each reason is read alone on its box or edge, starts with
  a headline, and writes names in backticks. Tried on one PR. Open: with every change
  needing a verdict and "question" only where no reason can be found, the agent found a
  reason for everything and answered "fine" throughout.
- Nolli prepares (`review.md`) and displays (verdicts) but calls no model.
- Default view of a diff: unfold containers with changes first; unchanged boxes count half
  against the budget because they are faded.
- In review mode red means removed, so cycles get a dotted border instead of a red one.
- No selection: the drawing has one highlight, the hover glow. The review list points at
  an item with it: hovering a row lights the item's boxes and edges, going to an item (a
  click on the row, j / k, or `item=C3` / `item=V2` in a link) unfolds what it needs, glides
  to it, flashes it and keeps its card open; nothing stays lit. An item's links (diff, hunks, the references that
  changed) open in place under its row. A selection with its own colour and a details pane
  for any clicked box was tried first and dropped: two highlight concepts, and the item
  list already covers every change.
