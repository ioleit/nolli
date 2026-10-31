---
name: nolli-review
description: Architecture review of a code change with nolli - a branch, a commit range or a GitHub pull request. Builds the diagram of what the change does to the repository's structure, judges each changed box and edge (fine / question / problem), checks the verdicts and opens them in nolli's viewer. Use when the user asks to review a change, branch or PR with nolli, or for an architecture or structural review of a change.
---

# Architecture review of a change with nolli

nolli draws a repository's architecture (modules, packages, their dependencies, HTTP
routes, deployment) and, for a change, what the change did to it. You review that
picture: a verdict on each box or edge that matters, which the user then sees on the same
diagram in nolli's viewer. nolli calls no model; you are the reviewer.

## 1. The change

What the user named, as `nolli view` takes it:

- nothing: the current branch against the default branch;
- a branch or commit: what it adds to the default branch;
- `<base>..<head>`, or `<base>...<head>` (from where they diverged);
- a GitHub pull request: its URL, or `'#123'` (quoted: `#` starts a shell comment). nolli
  asks GitHub for the PR's base and head and fetches them, so the change is the one GitHub's
  "Files changed" tab shows, whatever the local branches are.

## 2. The brief

```
nolli agent brief <change>
```

Run it in the repository (or add `--repo <path>`). If `nolli` is not found, tell the user
it needs installing (`pipx install nolli`, or from its repository) and stop. It builds the
change's dataset (a few seconds; reused if it exists) and prints JSON on stdout (what it
resolved and its progress go to stderr):

- `brief`: `review.md`, your instructions, the diagram and the change's description;
- `verdicts`: where your answer goes (`verdicts.json`); `has_verdicts`: one exists already;
- `inspect`: the `git diff` command for exactly this change;
- `check`, `view`: the commands for steps 4 and 5.

If `has_verdicts` is true and the user did not ask for a new review, go to step 5.

## 3. Review

Read `brief` and do what it says; it defines the order of work, what to judge, and the
answer's format. Read code only as the change has it: `inspect` for the diff, and
`git show <head>:<path>` for a whole file at the head. The working tree may be on another
commit or have uncommitted edits; don't read files from it. Don't change anything in the
repository.

Write your verdicts to the `verdicts` path: a JSON list of
`{"target": ..., "verdict": "fine" | "question" | "problem", "reason": ...}`.

## 4. Check

```
nolli agent check <dataset>
```

Use the `check` command the brief printed. It prints one line per problem (a node id not in the diagram, an edge the diagram does not
draw or draws the other way round, a missing reason, …) and exits 1. Fix each in
`verdicts.json` and check again until it prints `"ok": true`.

## 5. Show it

Start the viewer in the background; it serves until stopped:

```
nolli view <dataset> --no-open
```

It prints `viewer: http://127.0.0.1:<port>/#d=<name>` (to stderr; redirect it to a file to
read it). Give the user that URL, and say how to stop the viewer (stop that process). On
the user's own machine you can drop `--no-open` to open their browser; if they look from
another machine, `--host <an address of this one they can reach>` (and `--port`). The
viewer opens folded on the worst verdict, with your reasons on the boxes and edges.

Then tell the user, briefly: how many verdicts of each kind, and each `problem` and
`question` in one line, by the name the diagram gives the box or edge.
