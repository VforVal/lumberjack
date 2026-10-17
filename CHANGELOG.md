# Changelog

## Unreleased

- Run hooks, setup and cleanup directly with Python 3.11+ (`python` on PATH).
  No uv or runtime packages are required. Re-run setup to upgrade existing git hooks,
  and review the updated plugin hook commands in the harness.
- `/lumberjack:cleanup`: a `dirty` landed worktree is offered too, and on a `dirty` or
  `ignored files` block the command groups the stray files by what produced them and asks
  once per group whether to delete, ignore or commit them, then retries `--remove`. The
  `lumberjack` skill gives agents the same rule for a `blocked ...: dirty` line.

## 1.1.1

- Post-merge hook: setup and the sweep no longer record a `lumberjack.script` inside a
  removable linked worktree, and every generated hook skips quietly when the recorded
  script file is missing. Re-run setup to upgrade an existing hook.
- Removal lines: a detached worktree prints `cleaned (detached) <path>` without
  `(local branch kept)`; a `git worktree remove` that fails after unregistering the
  worktree prints `cleaned ... (local branch kept; directory left)` instead of `blocked`.

## 1.1.0

- `/lumberjack:cleanup`: a user-invoked command that runs the cleanup and
  `git worktree prune`, then asks about each `unverified` and `landed-probable`
  worktree and removes only the confirmed ones, keeping their branches.
- `sweep_worktrees.py --remove <path>`: removes that one worktree and keeps its branch;
  refuses with `current checkout`, `dirty`, `evidence`, `ignored files`, `prototype`,
  `primary checkout`, `no branch` or `not a worktree`, and exits 1.

## 1.0.0

First public release.

- Worktree sweep: at session start and after every merge, removes worktrees whose tip has
  landed in `origin/<primary_branch>` (tiers `integrated`, `content-integrated`,
  `landed-pr`) and keeps the branch; reports `landed-probable` and `unverified` tips,
  blocked worktrees, dependent branches and orphan directories as a summary line plus one
  line per event.
- Stop gate: blocks the end of a turn when the branch name or the primary checkout is wrong.
- `/lumberjack:setup`: a one-time interview that writes `.lumberjack.toml`, the post-merge
  hook block (plain, husky, lefthook, pre-commit, simple-git-hooks or an existing hook),
  optional `.claude/settings.json` pinning, the `CLAUDE.md` / `AGENTS.md` block and
  `.gitignore` entries; never commits.
- Plugins for Claude Code and Codex from one repository.
