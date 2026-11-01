# Changelog

Releases of [nolli-review](https://pypi.org/project/nolli-review/) on PyPI.

## Unreleased

### Changed

- **A verdict on an edge sits on the edge.** ELK lays its mark out as the edge's label, so
  the edge runs through the middle of the mark, and room is made for it, instead of the
  viewer guessing a spot near the route's middle bend.
- **"fine" verdicts are marked in the diagram too**, with their grey ✓.
- **Hovering an edge lights the boxes at its two ends**, as hovering a box lights its
  edges. Every hover, verdict mark and review item lights the same set for the same box or
  edge, and its card keeps clear of whatever is lit (and of the titles of unfolded boxes).
- **A hover card goes as near as it can without covering anything.** Each spot beside a box
  or edge moves away from it step by step, out to the edge of the canvas, until it covers
  nothing; covering something is the last resort, the less the better.

## 0.6.0

### Changed

- **`nolli serve` knows no GitHub App any more.** It asks GitHub's API with
  `NOLLI_GITHUB_TOKEN`, or as nobody. `NOLLI_GITHUB_APP_ID`,
  `NOLLI_GITHUB_APP_PRIVATE_KEY[_FILE]` and `NOLLI_GITHUB_APP_INSTALLATION_ID` are no longer
  read, and `[serve]` no longer installs `cryptography`. A service built on `nolli serve`
  brings its own: `make_server()` and `main()` in `nolli.viewer.hosted` take a `Token`
  (which token to ask with, per repository), a subclass of `HostedHandler` (pages and
  routes of its own) and a reviewer (`nolli.reviewer.Reviewer` is nolli's agent; another
  reads the same dataset and leaves verdicts that pass nolli's check). A reviewer's reason
  not to run is shown as it is when the viewer has no text of its own for it.
- **nolli's own CI comments as `github-actions`**: the workflow no longer signs in as a
  GitHub App with a private key kept in the repository's secrets.

## 0.5.0

### New

- **Reviews in the viewer.** With `ANTHROPIC_API_KEY` set where nolli runs, "Review
  structural changes" starts a review agent on the server (`nolli view`, and `nolli serve`
  for every visitor, on the key's owner). You can keep looking around while it runs:
  one line shows the step, the time and Cancel. Without a key the panel says how else to
  get a review. Where verdicts came from (CI, an agent run, a file) is recorded in
  `review.json` and shown in the Review heading.
- **CI verdicts in `nolli serve`**: a pull request's page reads the verdicts nolli's CI
  left in the PR's comments for its head commit, and opens on the worst one. Only the
  bots' comments count. Verdicts from before 0.4 are translated to today's ids.
- **GitHub App token for `nolli serve`**: `NOLLI_GITHUB_APP_ID` and
  `NOLLI_GITHUB_APP_PRIVATE_KEY` (or `NOLLI_GITHUB_APP_PRIVATE_KEY_FILE`) let the server
  ask GitHub's API with the app's installation token, 5,000 requests an hour instead of
  60 (`NOLLI_GITHUB_APP_INSTALLATION_ID` picks the installation; `NOLLI_GITHUB_TOKEN`
  takes any token). A repository GitHub says is private is still not shown, and clones
  stay anonymous. The startup line names the GitHub access.
- **Extras**: `nolli-review[api]` for the review agent, `nolli-review[serve]` for running
  `nolli serve` as a service (the agent, and `cryptography` for the app's token).

### Changed

- **`nolli serve` builds in processes of their own**, `--builds` at once (default: the
  CPUs, at most 4), so a server with more CPUs builds more at once. Anyone can ask for any
  public repository, so there are limits: `--timeout` (default 600 s) stops a longer
  build, and `--max-repo` (default 1G, GitHub's size of the repository) refuses a larger
  one before anything is fetched. Each has an environment variable: `NOLLI_BUILDS`,
  `NOLLI_BUILD_TIMEOUT`, `NOLLI_MAX_REPO`.
- The panel's head says only what is compared; the review's counts and origin are the
  heading of the Review section.

## 0.4.0

### New

- **HTML and CSS**: what pages and stylesheets load is extracted (`<script src>`,
  `<link href>`, images and media, `<style>` blocks, CSS `@import` and `url()`). A script
  from a package CDN is that npm package. HTML and CSS have the role "markup": shown by
  default, but they don't make a directory source.
- **Mermaid follows the viewer**: the viewer has three tabs on one scene, Interactive,
  Mermaid and JSON. The Mermaid tab follows the controls, can be copied, and unfolds a box
  on click. Presets (`p=default|folded|changes|unfolded`) name a cut in a link and replace
  the "default cut", "fold all" and "unfold changes" buttons. `default.mmd` and
  `review.mmd` are rendered from the same scene, and are closer to the drawing.
- **`nolli serve` extracts each commit once**: extractions are kept by commit under
  `<cache>/extractions`, so a pull request's base is extracted once for all its pushes.
  `--keep 20G` (or `NOLLI_CACHE_KEEP`) keeps the cache within a size; after each build,
  what was used longest ago is deleted.

### Changed

- **nolli in CI comments once per commit**: the example workflow no longer edits one
  comment but adds one for each pushed commit, so the PR's conversation reads commit,
  review, commit, review. Each commit's result stays downloadable (`nolli view`) until
  its artifact expires; a re-run on the same commit updates that commit's comment.
- **Entity ids are addresses in a fixed set of namespaces** (`nolli/model/address.py`):
  `repo:` for directories, files and the classes and types in them
  (`repo:app/store.py#Store`), `api:` for operations (`api:http:<app>/items/{}#GET`, path
  parameters normalised), `pkg:` for packages and images (package URLs:
  `pkg:npm/elkjs`, `pkg:python/yaml`, `pkg:build/web/Dockerfile`), `runtime:` for
  services and hosts, `config:` for environment variables. The groups in the Show filter
  are these namespaces: Artifacts holds packages and images, Runtime services and hosts.
  The model rejects an entity or relation outside them. Datasets and verdicts from
  earlier versions no longer load; extract again.
- **Hover texts are Markdown**, rendered in one place in the viewer. File names, values
  and counts are code spans, and docstrings are quoted with their own code spans kept.
  Pinned cards show as they are, wherever you open them from.

## 0.3.0

### New

- **`nolli serve`**: the viewer as a service. It answers GitHub's paths
  (`/<owner>/<repo>/pull/<n>`, `/commit/<sha>`, `/<owner>/<repo>`) on your own host,
  for public repositories only. The first visit builds the dataset; later visits find it
  in the cache (`--cache`).
- **nolli in CI**: an example workflow (`.github/workflows/nolli.yml`) runs a review on
  every push to a pull request and keeps one comment on it up to date: the structural
  changes, the worst verdicts with their marks, and the Mermaid diagram of the change.
  The dataset is uploaded as a workflow artifact; `nolli view <owner>/<repo>#<n>@<head7>`
  downloads and opens it. New command `nolli ci comment` writes that comment.
- **TypeScript and JavaScript go past imports**: classes, interfaces, type aliases and
  enums, and the references between them, also through barrels (`index.ts`) and
  namespaces. Type-only dependencies are drawn dotted. Reads tsconfig `paths`/`baseUrl`,
  workspace packages and CommonJS `require`. Declaration files (`.d.ts`) are named as
  their module.

### Changed

- Viewer: the sidebar folds to a strip and starts folded; the review's controls sit in a
  bar under the verdict list, which follows the Files list.
- Viewer: every edge has the same width.
- Review cards sum up the verdicts rolled up from inside a box.
- Verdicts: "good" is a green thumbs up; "fine" is grey and its rows fade. The review
  brief asks for reasons that say "this change", one sentence for "fine", and "good" for
  structural gains.

### Internal

- Languages are plug-ins in one registry instead of names spread through the core.
- Build functions moved from the command line into `nolli/build.py`.
