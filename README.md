# Nolli

Nolli draws a pull request as a change to a repository's architecture: the components,
modules and classes it touches, the dependencies between them that it adds, removes or
changes, and how much each one changed.

![A pull request in Nolli's viewer: changed boxes and edges in orange, a removed dependency dashed red, verdict markers from a review agent](docs/review.png)

## How to use

```
nolli diff <repo> <base> <head> -o data/pr
python viewer/serve.py --data data
```

Open http://127.0.0.1:8720. `<base>` and `<head>` are any git revisions in `<repo>`
(for a GitHub PR, `git fetch origin pull/48/head:pr-48` first). The repository is read
through `git archive`; its working tree is not touched.

To install, from a clone of this repository (Python 3.11 or later):

```
pip install -e .
```

A single commit, without the diff:

```
nolli extract <repo> -o data/<name>
```

## Reviewing with an agent

`nolli diff` also writes `review.md`: a brief for a review agent with the change's
title (`--title`), a Mermaid diagram of everything that changed, and the PR's
description (`--description FILE`, or `pr.md` in the output directory). Give it to the
agent of your choice. It answers with a JSON list:

```json
[
  {"target": "n_app_api", "verdict": "question", "reason": "…"},
  {"target": "n_app_worker --> n_app_api", "verdict": "problem", "reason": "…"}
]
```

where `target` is a node or an edge of the diagram and `verdict` is `fine`, `question`
or `problem`. Saved as `verdicts.json` next to the dataset, the verdicts appear as
markers on the boxes and edges they are about, and the review column lists them with
their reasons, worst first. Nolli itself calls no model: the diagram is computed from
the code, and the same two commits always give the same picture.

## What it reads

| Framework | Status | Note |
|---|---|---|
| Python | ![supported](https://img.shields.io/badge/supported-2ea44f) | imports, classes and references between them |
| FastAPI, Starlette | ![supported](https://img.shields.io/badge/supported-2ea44f) | routes from `@router.get(...)`-style decorators, `APIRouter` prefixes, `include_router` chains |
| Flask | ![partial](https://img.shields.io/badge/partial-e36209) | `@app.get(...)`-style routes; `@app.route` and blueprint prefixes are not read |
| `os.environ`, `os.getenv` | ![supported](https://img.shields.io/badge/supported-2ea44f) | environment variables a module reads |
| TypeScript, JavaScript | ![supported](https://img.shields.io/badge/supported-2ea44f) | imports between modules (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`) |
| `fetch`, axios, `EventSource`, `WebSocket` | ![heuristic](https://img.shields.io/badge/heuristic-dbab09) | HTTP calls, bound to the backend route with the same path shape; one level of helper function is followed |
| `process.env`, `import.meta.env` | ![supported](https://img.shields.io/badge/supported-2ea44f) | environment variables a module reads |
| docker-compose | ![supported](https://img.shields.io/badge/supported-2ea44f) | services, `depends_on`, service URLs in environment values, the variables each service sets |
| Dockerfile | ![supported](https://img.shields.io/badge/supported-2ea44f) | which directories go into an image (`COPY`, `ADD`) and its entrypoint |

Other languages show up as files and directories only.

## The name

[Giambattista **Nolli**](https://en.wikipedia.org/wiki/Giambattista_Nolli), commissioned
by Pope Benedict XIV, surveyed Rome from 1736 and published his ichnographic plan of the
city in 1748, by far the most accurate map of Rome up to then. It draws buildings as
solid blocks but leaves enclosed public spaces open, the interiors of churches and
courtyards among them: the plan shows what is inside where it matters for moving through
the city.

## License

Apache 2.0, see [LICENSE](LICENSE).
