# Nolli

Nolli draws a pull request as a change to a repository's architecture: the components,
modules and classes it touches, the dependencies between them that it adds, removes or
changes, and how much each one changed.

> [!NOTE]
> [Giambattista **Nolli**](https://en.wikipedia.org/wiki/Giambattista_Nolli), commissioned
> by Pope Benedict XIV, surveyed Rome from 1736 and published his ichnographic plan of the
> city in 1748, by far the most accurate map of Rome up to then. It draws buildings as
> solid blocks but leaves enclosed public spaces open, the interiors of churches and
> courtyards among them: the plan shows what is inside where it matters for moving through
> the city.

## How to use

```
pip install nolli
```

### Viewing the current state

In a git repository:

```
nolli view --whole
```

### Viewing changes

```
nolli view                  # your current branch
nolli view '#48'            # a GitHub pull request (or its URL)
nolli view <base>..<head>   # any two revisions
```

### Reviewing changes with an agent

Install the skill for your coding agent:

```
nolli agent skill --install ~/.claude/skills
```

Then tell your agent to "review PR #48 with nolli". It gives you a link to the viewer,
with its verdicts on the structure (fine, question, problem, with reasons) on the boxes
and edges they are about.

![A pull request in Nolli's viewer: changed boxes and edges in orange, a removed dependency dashed red, verdict markers from a review agent](docs/review.png)

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

## License

Apache 2.0, see [LICENSE](LICENSE).
