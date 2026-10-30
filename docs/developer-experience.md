# Developer experience: reviewing a PR with nolli

What using nolli to review a pull request should feel like, as the owner described it
(2026-10-30). This is a target, not a design: nothing below is decided except the goal.

## The goal

One install, then the same review that was done by hand on this repository's own PR #7:

```
pip install nolli
```

and from there, on any repository with a GitHub remote:

1. Point nolli at a pull request.
2. It builds the diff of the PR (both commits, merged into one model) and the review brief
   (`review.md`: the PR's title, the diagram with every change unfolded, how to review,
   the PR's description).
3. A review agent answers the brief with verdicts on the diagram's boxes and edges.
4. The viewer opens on the review: folded, on the worst verdict, with the review column
   and the hover cards.

## How it was done by hand (PR #7, 2026-10-30)

For reference, the steps that the one command should replace:

- `gh pr view 7 --json title,body,commits` written into `pr.md` in an output directory
  (`~/data/nolli/nolli-pr7`), by hand.
- `nolli diff ~/work/nolli <merge-base> <head> -o <out> --pr 7 --title "…"`, with the
  merge base and head looked up by hand.
- The PR added to a local, git-ignored corpus list (`dev/corpus.local.toml`) so that
  `dev/extract_corpus.py` rebuilds it with everything else.
- A review agent (a Claude Code subagent) started with a short hand-written prompt:
  read `review.md` and do what it says, the code is in this repository, read it only with
  `git diff` / `git show` for these two commits, write `verdicts.json` there.
- The viewer (`viewer/serve.py --data ~/data/nolli`) started from a checkout of this
  repository, then opened at `#d=nolli-pr7`.

## Done so far (2026-10-30, branch `dx-view`)

- `nolli view [<change>]` on any git repository, plain git only: the current branch, a
  branch or commit, `A..B` or `A...B` (`nolli/change.py`). It builds the dataset into a
  default data directory (reused per change and nolli version), serves the viewer and
  opens the browser. Title and description come from the commits.
- The viewer is in the package (`nolli.viewer`) and offline (`viewer/vendor/`).
- Tested on 2026-10-30 as a user would before PyPI: `pip install git+file://…@dx-view`
  into a fresh venv outside the checkout, then `nolli view` in a fresh clone of autoforge
  on the branch of its PR #213 (origin's default branch set back to the PR's base), and on
  its merge commit; headless Chromium with every non-local request blocked: no blocked or
  failed requests, no console errors, ELK, fCoSE and Mermaid all drew. The model was the
  same as the hand-made `autoforge-pr213` dataset's; only the PR links were missing (no
  GitHub remote in that clone, no PR number).

Next: the GitHub layer (`nolli view <PR url | #N>`: base, head, title, description and
links from the PR), then the brief and verdict check for a review skill.

## What was missing (as of 2026-10-30, before the above)

Checked against the code on that date; to be re-checked by whoever picks this up.

- **The viewer is not in the package.** `pyproject.toml` packages only `nolli*`; the
  viewer is `viewer/` next to it, and `serve.py` imports nolli from the checkout it sits
  in. There is no `nolli serve` (or `nolli view`) command.
- **The viewer needs the internet.** ELK.js, Mermaid and Cytoscape are loaded from
  jsDelivr at run time (`viewer/index.html`, `js/mermaid.js`, `js/layout/fcose.js`).
- **No PR input.** `nolli diff` takes a repository path and two revisions; the PR's
  number, title and description are passed by hand (`--pr`, `--title`, `pr.md` or
  `--description`). Nothing resolves a PR URL or number to its base, head and text.
- **No place for datasets.** Output directories are chosen by hand; the viewer lists the
  directories under one `--data` path.
- **No agent step.** Nolli calls no model, by design so far. The prompt that started the
  agent lives nowhere in the repository. Open: whether nolli only writes the brief (and
  prints the prompt for whatever agent the user runs), or can also start one.
- **The package name.** Whether `nolli` is free on PyPI is not checked.

## Not in scope here

- What the review should judge, and how the brief words it: see NOTES, "Review mode".
- A CI / GitHub Action that runs this on every PR: a later step, on top of this one.
