# Nolli

Nolli draws a pull request as a change to a repository's architecture: the components,
modules and classes it touches, the dependencies between them that it adds, removes or
changes, and how much each one changed.

> [!NOTE]
> [Giambattista **Nolli**](https://en.wikipedia.org/wiki/Giambattista_Nolli) spent twelve
> years on a single map. Commissioned by Pope Benedict XIV, he began surveying Rome in
> 1736, street by street and building by building, and only in 1748 did he publish his
> ichnographic plan of the city: by far the most accurate map of Rome anyone had made.
> Its buildings are solid blocks, but the city's enclosed public spaces are left open,
> the interiors of churches and courtyards among them. The plan shows what lies inside
> exactly where it matters for moving through the city.

## How to use

```
pip install nolli-review
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
with its verdicts and reasoning on the structural changes.

Or let the viewer run the review: with an Anthropic API key set, the viewer's "Review
structural changes" button starts a review agent of its own and shows its verdicts when
it is done.

```
pip install 'nolli-review[api]'
ANTHROPIC_API_KEY=sk-ant-… nolli view '#48'
```

### Serving it

```
nolli serve
```

serves any public GitHub repository, pull request or commit under GitHub's own paths:
`/<owner>/<repo>/pull/48` shows what `nolli view https://github.com/<owner>/<repo>/pull/48`
shows. A pull request's page shows the verdicts nolli's CI left for its head commit, if
there are any. With `ANTHROPIC_API_KEY` set, anyone who can open the server's pages can
start a review on it.

Without a token the server asks GitHub as nobody, which allows 60 requests an hour. A
GitHub App's token raises that to 5,000: set `NOLLI_GITHUB_APP_ID` and
`NOLLI_GITHUB_APP_PRIVATE_KEY` (or `NOLLI_GITHUB_APP_PRIVATE_KEY_FILE`), and
the app installed on its owner's account (its token is used, whoever else installed it); or give any
token as `NOLLI_GITHUB_TOKEN`. Private repositories are never shown, whatever the token
can see.

```
pip install 'nolli-review[serve]'
```

![A pull request in Nolli's viewer: added edges in green, a removed module dashed red, verdict marks from a review agent in chess notation, and the card of the worst one, a new import cycle](https://raw.githubusercontent.com/ioleit/nolli/main/viewer/e2e/__screenshots__/review.spec.js/blunder-on-edge.png)

## What it reads

| Framework | Status | Note |
|---|---|---|
| Python | ![supported](https://img.shields.io/badge/supported-2ea44f) | imports, classes and references between them |
| FastAPI, Starlette | ![supported](https://img.shields.io/badge/supported-2ea44f) | routes from `@router.get(...)`-style decorators, `APIRouter` prefixes, `include_router` chains |
| Flask | ![partial](https://img.shields.io/badge/partial-e36209) | `@app.get(...)`-style routes; `@app.route` and blueprint prefixes are not read |
| `os.environ`, `os.getenv` | ![supported](https://img.shields.io/badge/supported-2ea44f) | environment variables a module reads |
| TypeScript, JavaScript | ![supported](https://img.shields.io/badge/supported-2ea44f) | imports, classes, interfaces, type aliases and enums, and references between them through barrels (`index.ts`) and namespaces; type-only dependencies drawn dotted; tsconfig `paths`/`baseUrl`, workspace packages, CommonJS `require` |
| `fetch`, axios, `EventSource`, `WebSocket` | ![heuristic](https://img.shields.io/badge/heuristic-dbab09) | HTTP calls, bound to the backend route with the same path shape; one level of helper function is followed |
| `process.env`, `import.meta.env` | ![supported](https://img.shields.io/badge/supported-2ea44f) | environment variables a module reads |
| HTML | ![supported](https://img.shields.io/badge/supported-2ea44f) | what a page loads: `<script src>`, `<link href>`, images and media, `<style>` blocks; a script from a package CDN is that npm package; paths from the server root (`/src/main.tsx`) are matched heuristically; links between pages are not dependencies |
| CSS, SCSS, Less | ![supported](https://img.shields.io/badge/supported-2ea44f) | `@import` and `url()`; `@import "tailwindcss"` is that npm package |
| docker-compose | ![supported](https://img.shields.io/badge/supported-2ea44f) | services, `depends_on`, service URLs in environment values, the variables each service sets |
| Dockerfile | ![supported](https://img.shields.io/badge/supported-2ea44f) | which directories go into an image (`COPY`, `ADD`) and its entrypoint |

Other languages show up as files and directories only.

## License

Apache 2.0, see [LICENSE](LICENSE).
