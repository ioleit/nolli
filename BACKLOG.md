# Backlog

Roughly in the order to pick them up. Each item names what is wrong or missing, not a
solution; solutions are for whoever picks it up.

## Next

- **Deployment relationships.** Compose services, images, `COPY` allocation, env
  wiring and runtime URLs are extracted and look valuable, but the current rendering
  (chips on code boxes, a folded "Deployment" group) is a first guess.
  Experiment with different renderings before deciding how this layer should look.
- **Rendering, open-ended.** The ELK orthogonal edge routing reads like a circuit board
  once there are more than a few dozen edges; labels are tiny; layout direction, edge
  routing (splines, bundling along the containment tree), emphasis and reading order are
  all unexplored beyond defaults. Unfolding animates and keeps the clicked box in place, but the layout
  is still computed from scratch, so other boxes can travel far and cross each other
  mid-step (bench.html measures how much of the arrangement survives).
  ELK's "each box separately" mode kept more (order 0.91 vs 0.81 over the corpus), but
  its edges between nesting levels were straight lines that cut through boxes; it was
  removed with the force layout (fCoSE) when ELK became the only layout.
  bench.html averages steps equally, so a step that keeps only 2 boxes (1 pair) can
  score 0 or 1 and swing the worst-step column.
- **Review brief and check.** Agents reviewing with the skill asked whether a changed
  group box needs a verdict of its own and which of a module's two ids to use, and
  `nolli agent check` does not notice a changed box or edge left without a verdict.
- **Package-dir mappings.** An import of a package that `pyproject.toml` maps to another
  directory (`nolli.viewer` → `viewer/`) is drawn as an edge to the parent package, so a
  cycle through it is not drawn.
- **Tests for the roll-up rules.** Only extraction is tested; none of the rules in
  `nolli/rollup.md` has a test, and two broke silently while they were being built.

## Then

- Parse each Python file once; today imports, classes, routes and env reads each parse
  the file again (a 3,000-file repo takes 21 s, most of it parsing).
- The role rule hides a directory whose logic lives in HTML (the viewer's own directory
  disappears from the picture of this repo).
- Root routes (`GET /`, the SPA catch-all) sit as bare operations in the app box; the
  catch-all `/{path:path}` matches every frontend call the real routes don't; recognising
  that a backend serves the frontend build.
- APIs (`extractors/api.py`): put an API under the component that serves it instead of
  (or as well as) the APIs group; a second adapter (Express, Flask `@app.route`) and a
  second protocol (gRPC from `.proto`, exact matching is there) to test the shared
  structure; WebSocket calls from the frontend arrive as `GET` and don't meet
  `WEBSOCKET` routes.
- Re-exports as the package's provided interface (`provide(package, Name)`), the same for
  TypeScript `index.ts` barrels; today references are followed through them and the
  barrel's own edges into its folder are dropped.
- TypeScript: dynamic `import()` is marked but drawn like any import, though it often
  marks a lazily loaded part (code splitting); `declare module` and JSDoc
  `import("./x").T` types are not read; a local name shadowing an import is counted as
  the import; Vite/webpack `resolve.alias` outside tsconfig is not read.
- More front-ends and adapters: Rust (crates as modules and as components), Flask,
  Express, the Modal SDK (functions and web endpoints as interfaces, apps as deployment).
- The guessed numbers (budget, utility threshold, weak edge, fan size) into one named
  configuration.
- A place for a declared architecture (layers, allowed dependencies) next to the
  extracted one.
