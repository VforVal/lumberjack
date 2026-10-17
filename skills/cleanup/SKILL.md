---
name: cleanup
description: Remove landed worktrees in this repository, then remove unverified and landed-probable ones only after the maintainer confirms each
disable-model-invocation: true
---

# lumberjack cleanup

Clear old worktrees in the repository this command runs in. The sweep script does all
removal; you relay its lines and ask. Never remove a worktree the maintainer did not
confirm by name, and never pass `--force` to git.

Resolve the script at `../../scripts/sweep_worktrees.py` relative to this skill's
directory. Use its absolute path in place of `SWEEP` below, quoted for the active shell.
Run every command from the repository root. If `uv` is missing, use an available
`python` or `python3` version 3.11 or newer in place of `uv run --quiet --script`.

## 1. Sweep

```
uv run --quiet --script "SWEEP" --cleanup
git worktree prune --verbose
```

`--cleanup` fetches `origin` and removes landed worktrees (`integrated`,
`content-integrated`, `landed-pr`) that nothing blocks, keeping their branches.
`git worktree prune` drops records whose directories are gone. Relay both outputs
verbatim. A `skipped` line or a `lumberjack:` config error ends the sweep step; still
go on to step 2.

## 2. List what is left

```
uv run --quiet --script "SWEEP"
```

Keep the `worktree` lines whose `tier=` is `unverified` or `landed-probable`. Show them
as one numbered list: branch, path, tier and reason. The path is everything after the
branch, already absolute; use it as `PATH` below. If there are none, say so and stop.

## 3. Ask about each one

One worktree at a time, in list order. Ask whether to remove it, and say its branch is
kept. Only an explicit yes for that worktree counts; anything else, silence included,
means keep it. A yes to one is not a yes to the next. On a yes, run:

```
uv run --quiet --script "SWEEP" --remove "PATH"
```

Relay its one line verbatim: a `cleaned` line and exit 0 (`directory left` means the
maintainer deletes the leftover directory by hand), or a `blocked` line with its reason
and exit 1, after which the worktree stays and you move on. Do not work around a block
(commit, stash, delete files or `cd` away) unless the maintainer asks for that step by
name. The one exception is stray files, handled below.

### Stray files (`dirty` or `ignored files`)

The block line names no files. Run `git -C "PATH" status --porcelain --ignored` and
group the untracked (`??`) and ignored (`!!`) entries by what produced them: one tool's
output, one file type, one directory. Never ask file by file. Ask one question per
group, naming its files and the three choices:

- **delete**: `git -C "PATH" clean -f -- <files>` (add `-d` for directories, `-x` for
  ignored ones); removal would delete them anyway.
- **ignore**: add one `.gitignore` pattern on the branch and commit it, then delete the
  copies as above; `--remove` still refuses while non-cache ignored files exist.
- **commit**: `git -C "PATH" add -- <files>` and commit on the branch, so they ride its
  pull request; the tip is then unpushed, which does not block `--remove`.

Only the answer for that group counts; silence keeps its files, and the worktree stays
while any group is kept. Modified tracked files (`M`, `D`) are not stray: show them and
leave them unless the maintainer names the step. Once every group is settled, run
`--remove "PATH"` again and relay its line.

## 4. Report

List what was removed, what was kept and what was blocked with its reason. Mention
that a removed worktree comes back with `git worktree add <path> <branch>`.
