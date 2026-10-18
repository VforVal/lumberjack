---
name: lumberjack
description: Worktree and branch rules delivered by lumberjack at session start. Use as a reference when these rules are absent from context before creating, finishing, cleaning up or recovering worktrees or branches.
---

# Worktrees, cleanup and the Stop gate

lumberjack keeps one clone tidy: the primary checkout stays on `primary_branch`, work happens
in one worktree per task, and a landed worktree is removed automatically. Settings live in
`.lumberjack.toml` (or `[tool.lumberjack]` in `pyproject.toml`); the README lists each key's default.

## Primary checkout

It stays on `primary_branch`, clean, and only fast-forwards from `origin/<primary_branch>`.
Do not edit features there. Create a worktree from fresh `origin/<primary_branch>`
under one of `worktree_roots`, one per task, so it inherits nothing uncommitted.

## Branch names

`<owner>/<ticket_prefix><n>-<slug>` or `<owner>/<slug>`, where `<owner>` is one of
`owners` (empty means any lowercase owner). Throwaway captures use
`<prototype_prefix><ticket_prefix><n>-<slug>`. Other shapes fail the Stop gate.

## Cleanup rule

Each session start the sweep fetches `origin` (5 second limit; on failure it says
`origin stale` and carries on), then removes a worktree only when all of these hold:

1. Its tip is verified landed in `origin/<primary_branch>`: an ancestor, identical
   content, or the exact tip of a merged pull request (checked through `gh`). Squash and
   rebase merges count; a guessed match or an unreachable `gh` leaves it unverified.
2. `git status --porcelain` is empty. Ignored files do not count; untracked ones do.
3. The tip is pushed (upstream contains it); waived when a merged pull request had this tip.
4. `evidence_dir`, when set, is empty or absent inside the worktree.
5. The branch is not a `prototype_prefix` branch, which is never touched.

A worktree that fails a check stays, and the sweep names the reason: `dirty`, `unpushed`,
`evidence`, `tip diverged` (the pull request merged a different tip), `current checkout`
(the sweep runs inside it), `locked` (`git worktree lock`) or `fresh` (added <24h ago, no commits).
The local branch is always kept. Orphan directories in a worktree root holding only caches
(`node_modules`, `.venv`, `__pycache__`, tool caches) are deleted; other orphans are listed.
With the primary off `primary_branch`, or a post-merge run in another worktree, nothing is cleaned.
On `dirty`, list the stray files grouped by what produced them and ask the maintainer once
per group (delete, ignore or commit), never file by file; `/lumberjack:cleanup` has the steps.

When a landed branch has other branches built on top, the sweep prints one line per
dependent, `dependent <b> of <a>: git rebase --onto <primary> <a-tip> <b>`. Run it
before pushing `<b>`, otherwise its pull request still carries the squashed commits.

## Keys that change the behavior

- `cleanup = "prune"` (default) removes eligible worktrees. `"report"` prints
  `landed <branch> <path>: would remove` and keeps them. `"keep"` keeps them silently.
- `stop_gate = false` turns the Stop hook off; it then exits 0 without output.
- A config error (including a wrong value type or an empty `prototype_prefix`) is printed
  at session start and the cleanup does nothing; the Stop gate blocks until it is fixed.

## What the sweep prints

`sweep: cleaned N, landed N, dependents N, blocked N, unverified N, orphans N`, with
`, origin stale` appended when the fetch failed, then one line per event: `cleaned`,
`landed`, `blocked`, `dependent`, `orphan` or `hint`. When nothing was swept the only
line says why: a config error, or `sweep: ...; skipped`. The exit code is always 0.
Paths are relative to the primary's parent directory, or start with `~`.
`unverified` counts unproven branches; only the maintainer's `/lumberjack:cleanup` removes them.

## Stop gate

At turn end, the Stop hook sends a JSON block decision with a reason when the current
branch breaks naming rules or the primary checkout is off `primary_branch`. Rename
with `git branch -m`, move work to a worktree, then return the primary to `primary_branch`.
A second stop in the same turn passes; `stop_gate = false` disables the gate. Plain
`--gate` uses exit 2 and stderr; the plugin's `--hook` returns exit 0 and JSON.

## Recovery

Removing a worktree deletes nothing committed: `git worktree add <path> <branch>` brings it back.
Uncommitted files are not recoverable, which is why a dirty worktree is never removed.
Before forcing a removal by hand, snapshot the branch: `git update-ref refs/archive/<branch> <branch>`.
