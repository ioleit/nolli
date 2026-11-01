# Changelog

Releases of [nolli-review](https://pypi.org/project/nolli-review/) on PyPI.

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
