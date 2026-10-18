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
use the name that reports 3.11+ in place of `python` below. If missing or older, stop and point
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

## 4. Work documents

```
python "SWEEP" --json
```

Read `docs`, `doc_strategy` and `trash`. When no doc has `state` `linked` and `trash` is
empty, skip to step 5. Never remove a bundle outside this step's commands, and never
by hand.

### Strategy (first run only)

When `doc_strategy` is `{}`, ask in one message, with these choices, then save the answers:

- scope: `markdown` (only `.md` files) or `bundle` (whole entries, run output included)
- ephemeral bundles: `trash` (recoverable), `delete` (permanent) or `keep`
- reference bundles: `promote` or `keep`
- trash days: whole days a trashed folder is kept (suggest 30)
- tracker: where tickets are checked, in words (for example `github issues`)
- doc roots to always keep, if any (saved as `roots`, each root mapped to `keep`)

Write them as JSON with keys `scope`, `ephemeral`, `reference`, `trash_days`, `tracker`
(plus `roots`, a table of root to `keep`, when they named any) to a temporary file and run `python "HELPER" strategy FILE`, where `HELPER` is
`../../scripts/setup_lumberjack.py` relative to this skill's directory. Relay its output and
say the maintainer commits `.lumberjack.toml`. If they decline, remove nothing and go to
step 5. A saved strategy is never asked for again; follow it.

### Offer

Take the `linked` docs whose class is not `keep` in the strategy and whose `root` is not
mapped to `keep` in `doc_strategy.roots`. For each, check every id
in `tickets` in the tracker with your own tools (for example `gh issue view` or a tracker
connector). Offer a bundle only when all of its tickets are confirmed closed; an unreachable
tracker or an unknown ticket means skip it. Ask once per group (same class and doc root),
never bundle by bundle, listing the paths, tickets and sizes. Only an explicit yes counts.

- **ephemeral**: say whether the strategy trashes (kept `trash_days` days) or deletes
  (permanent, no copy). On a yes, run `python "SWEEP" --clear "PATH"` for each path.
- **reference**: offer only *promote* or *keep*; keep is the default. To promote, write
  the findings into a document without naming the bundle's folder and get it tracked on
  `origin/<primary_branch>` through its pull request, or push the work as a
  `prototype_prefix` branch. Only then run
  `python "SWEEP" --clear "PATH" --promoted "REF"` with that document's path or branch name.

Relay each line verbatim. A `blocked` line (exit 1) means the bundle stays; do not work
around it.

### Trash

When `trash` lists folders, show them with their age and size and ask once whether to
purge them. On a yes, run `python "SWEEP" --purge-trash` and relay its lines. Without
a yes, leave them.

## 5. Report

List what was removed, what was kept and what was blocked with its reason (worktrees,
bundles and trash). Mention that a removed worktree comes back with `git worktree add <path> <branch>`.
