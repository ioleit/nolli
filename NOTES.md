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
  The picture is about architecture, not call graphs. Open for other languages: the
  "smallest unit with state and an interface" (struct+impl in Rust? package-level types
  in Go?).
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

- Python modules resolve relative to any ancestor directory of the importing file and
  to `src/`, `lib/`, `python/` roots; FastAPI `include_router` prefixes are followed
  through re-exports.
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

## Picture

- Default cut: unfold the box that reveals the most edges per node it adds (edges whose
  lowest common ancestor is the box), until a budget (34) is reached. Groups with no
  internal structure never unfold by themselves.
- Unfolding shows all children. A partially opened box hides problems. Only the − folds.
- Set-but-unread env variables are hidden; endpoints nobody in the repo calls are shown
  (they are the service's surface).
- Allocation (what is copied into which service) is drawn as coloured chips on the
  code box, not edges; env wiring (reader → setter) is hidden by default because it says
  the same thing. Both are open for different rendering (backlog).
- Numbers that are guesses: budget 34, utility threshold more than 6 incoming edges, weak
  edge under 5% and fewer than 3 references, fan bundling from 3 children.

## Focus

- A focus is a set of entities at any depth. Connections are looked for in the fact graph,
  not the drawn one, so a big folded box can't make two members look connected; everything
  inside a focused box counts as that box.
- "Connected" means every shortest directed path (use, provide reversed to interface →
  realiser) from one member to another, at most 6 steps, not walking through a third
  member; for a pair with none in either direction, the nearest shared dependency or user
  (up to 3). Shared dependencies are often widely used modules; whether that is
  an explanation or noise is open.
- The focus cut starts from "fold all", not the default cut: unfolding unrelated boxes
  works against the point. Members are always unfolded to; connectors only while the
  picture stays within the budget, nearest connections first.
- Five grades: focus, path, near (one drawn edge from a member), frame (a container
  around any of these) and context. Faded, not removed, so the layout keeps its shape and
  hovering brings anything back. Opacities are guesses.
- Shift-click as the gesture, because a plain click already unfolds. Changing the focus
  recomputes the cut; clearing it keeps the picture.

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
