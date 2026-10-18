# Changelog

## Unreleased

- The sweep, `--cleanup` and `--remove` no longer remove a worktree a session may have
  just started in: one added less than 24 hours ago whose branch has not moved since it
  was created is blocked as `fresh`, and one locked with `git worktree lock` is blocked as
  `locked`.
- `doc_roots`: a new list setting naming the folders that hold work documents. A landed
  worktree with an untracked or ignored file under one is kept with reason `docs` in
  cleanup, session start and `--remove`. `evidence_dir` is unchanged and still reports
  `evidence`. The `lumberjack` skill tells agents to write work documents only under a
  doc root, in a folder named for the ticket or branch slug.
- Run hooks, setup and cleanup directly with Python 3.11+ (`python` on PATH).
  No uv or runtime packages are required. Re-run setup to upgrade existing git hooks,
  and review the updated plugin hook commands in the harness. The plugin hooks run through
  a `git` alias so git's own `sh` picks the first of `python3` and `python` that reports
  3.11+ under any harness shell; the generated post-merge hooks do the same and skip when
  none does, and an older Python exits with `lumberjack: Python 3.11+ required, found
  <version>` instead of a traceback.
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
