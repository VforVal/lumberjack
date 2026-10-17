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
Run every command from the repository root with Python 3.11+ as `python` or `python3`;
use the name that exists in place of `python` below. If missing or older, stop and point
to https://www.python.org/downloads/ .

## 1. Sweep

```
python "SWEEP" --cleanup
git worktree prune --verbose
```

`--cleanup` fetches `origin` and removes landed worktrees (`integrated`,
`content-integrated`, `landed-pr`) that nothing blocks, keeping their branches.
`git worktree prune` drops records whose directories are gone. Relay both outputs
verbatim. A `skipped` line or a `lumberjack:` config error ends the sweep step; still
go on to step 2.

## 2. List what is left

```
python "SWEEP"
```

Keep the `worktree` lines whose `tier=` is `unverified` or `landed-probable`, plus any
worktree step 1 reported as `blocked ...: dirty`. Show them as one numbered list: branch,
path, tier and reason. The path is everything after the branch, already absolute; use it
as `PATH` below. If there are none, say so and stop.

## 3. Ask about each one

One worktree at a time, in list order. Ask whether to remove it, and say its branch is
kept. Only an explicit yes for that worktree counts; anything else, silence included,
means keep it. A yes to one is not a yes to the next. On a yes, run:

```
python "SWEEP" --remove "PATH"
```

Relay its one line verbatim: a `cleaned` line and exit 0 (`directory left` means the
maintainer deletes the leftover directory by hand), or a `blocked` line with its reason
and exit 1, after which the worktree stays and you move on. Do not work around a block
(commit, stash, delete files or `cd` away) unless the maintainer asks for that step by
name; a per-group answer under "Stray files" below is that request.

### Stray files (`dirty` or `ignored files`)

The block line names no files. Run `git -C "PATH" status --porcelain -uall --ignored`.
Stray entries are the untracked (`??`) ones and the ignored (`!!`) ones other than caches
(`node_modules`, `.venv`, `__pycache__`, tool caches). Modified tracked files (`M`, `D`)
are not stray: show them and leave them. Group the stray entries by what produced them:
one tool's output, one file type, one directory. Never ask file by file. Ask one question
per group, naming its files and the three choices:

- **delete**: `git -C "PATH" clean -f -x -- <files>`; removal would delete them anyway.
- **ignore** (`??` entries only): add one pattern to `.git/info/exclude` in the primary
  checkout, which covers every worktree of this clone without a commit, or to
  `.gitignore` on the primary branch through its own pull request; then delete the
  copies as above.
- **commit**: `git -C "PATH" add -f -- <files>`, commit on the branch and push; the files
  reach the primary branch through its pull request, a new one if the old one merged.

Only the answer for that group counts; silence keeps its files, and the worktree stays
while any group is kept. Once every group is settled, run `--remove "PATH"` again and
relay its line.

## 4. Report

List what was removed, what was kept and what was blocked with its reason. Mention
that a removed worktree comes back with `git worktree add <path> <branch>`.
