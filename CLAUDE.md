# nolli: working here

## Shared branch

Several sessions work on nolli at once. They keep in sync through the branch `shared`
on GitHub:

- Start a worktree from it: `git -C ~/work/nolli worktree add ~/work/wt/nolli-<task> -b <task> origin/shared`.
- Every commit is pushed to `origin/shared` by a post-commit hook in the lab clone
  (`~/work/nolli/.git/hooks/post-commit`, shared by all worktrees). It rebases your branch
  onto `origin/shared` first, so you also pick up the other sessions' commits.
- Read the hook's output after each commit. On `NOT PUSHED`, resolve as it says and run
  `git sync`. Never force-push `shared`.
- `main` is untouched by the hook; `shared` reaches `main` only when the owner asks.
