# Changelog

## Unreleased

- Website: the product site now lives in `website/` and deploys to GitHub Pages
  (https://vforval.github.io/lumberjack/) through `.github/workflows/site.yml`.
- README: new "What it runs and sends" section listing every command, network call and file
  change, for the Claude plugin directory review. The logo uses Markdown image syntax.

## 1.2.1

The first release after 1.1.1. Version 1.2.0 was set on main but never tagged; its changes ship
here, with `plugin update` now seeing a new version.

Upgrade: the hooks now need Python 3.11+ on PATH as `python3` or `python`; `uv` is no longer
used. Install Python 3.11+ if `uv` was your only source of it, then re-run `/lumberjack:setup`
to refresh the post-merge hook, pick `doc_roots` and answer which of them are durable: only a
durable root (`doc_reference` `["*"]`) keeps every bundle, prototypes included, from being trashed.

Doc cleanup is now by rule, not by prompt: once a `doc_strategy` is saved (`/lumberjack:cleanup`
asks for it once; with none saved nothing moves), every session start and post-merge sweep moves
each `linked` `ephemeral` bundle that has stayed linked for `grace_days` into
`.git/lumberjack-trash/<UTC timestamp>/`, under its repository path. Reference, live and keep-root
(`doc_strategy.roots`) bundles stay. Set `cleanup = "report"` to see `would trash` lines instead,
or `"keep"` to move nothing, worktrees included; move a bundle back out of the trash to restore it before `trash_days`
purges it.

- The sweep, `--cleanup` and `--remove` no longer remove a worktree a session may have
  just started in: one added less than 24 hours ago whose branch has not moved since it
  was created is blocked as `fresh`, and one locked with `git worktree lock` is blocked as
  `locked`.
- `doc_roots`: a new list setting naming the folders that hold work documents. A landed
  worktree with an untracked or ignored file under one is kept with reason `docs` in
  cleanup, session start and `--remove`. `evidence_dir` is unchanged and still reports
  `evidence`. The `lumberjack` skill tells agents to write work documents only under a
  doc root, in a folder named for the ticket or branch slug.
- The sweep lists the bundles (outermost untracked entries) of the primary checkout's
  `doc_roots` with their tickets, class (`ephemeral` or `reference`, set per root by the new
  `doc_reference` setting) and live, linked or unlinked state, and prints
  `doc <class> <path>: tickets <ids>; <size>` for each linked bundle it leaves.
- A bundle is also live while its folder name appears in a tracked file or in a commit
  message of an unlanded branch, and the `lumberjack` skill tells agents to add the current
  ticket to a bundle's `Tickets:` line when they read or extend it. A bundle listing two
  tickets stays live after the first one's branch lands.
- Setup suggests `doc_roots`: `discover` lists a `.scratch/` folder and ignored top-level
  folders holding Markdown as `doc_root_candidates` (names only, a bounded scan, no file
  reads), and the interview proposes them with a reason. A re-run keeps the saved value.
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
- `doc_strategy`: a new setting `/lumberjack:cleanup` asks for once when `doc_roots` holds linked
  bundles (`scope`, `trash_days`, `grace_days` and optional keep-`roots`) and saves with the new
  `setup_lumberjack.py strategy <file.json>`; a re-run of setup keeps it. Until it is saved nothing
  is removed. The command checks no tracker (a linked bundle is done by git alone), asks once per
  group, and offers `reference` bundles only promote or keep. A saved `doc_strategy` without
  `grace_days`, or with the old keys, is a config error: save it again with `strategy` (which
  replaces it whole) or the cleanup interview.
- Session start and the post-merge sweep trash every `linked` `ephemeral` bundle by rule, like a
  landed worktree, once a `doc_strategy` is saved and the bundle has stayed linked for `grace_days`
  (counted from the first session start that saw it linked, kept in
  `<git dir>/lumberjack-docs.json`; a bundle that turns live again starts over, a damaged file
  never trashes early): no prompt, never raising, each as
  `cleared <path>: trashed <timestamp>`, and `docs N` in the summary line counts the bundles trashed
  this run (a bundle inside its grace period stays, prints its `doc` line and is not counted).
  A bundle that cannot be moved prints `blocked <path>: <reason>` and counts in `blocked N`;
  a Markdown-scope bundle with no `.md` file left is skipped silently and, recorded in the same
  file, not walked again while it stays linked; one whose walk runs out of time although it started
  with more than half of trashing's time left prints `blocked <path>: too large to walk in time`
  once and is left to the cleanup command. One run at a time trashes and purges, through
  an operating-system lock on `<git dir>/lumberjack-docs.lock`, released at once when a run is
  killed: a session start that finds it held leaves docs alone; `--clear` and `--purge-trash` wait
  up to 10 seconds, then refuse with `blocked <path>: a sweep is trashing`
  (`blocked trash: a sweep is trashing`). A lock file that cannot be opened prints
  `docs: unavailable (<error>)` at session start. Markdown
  scope removes the folders its move empties. Reference, `unlinked`, live
  and keep-root bundles stay. `cleanup = "report"` prints `doc <class> <path>: would trash <N> files (<size>)`, moves
  nothing and still records first-seen dates; `"keep"` moves nothing, records nothing and prints
  nothing. Sizing gets 3 seconds; the Markdown walk, trashing and purging share 7 more, started
  after the branch checks, with the last 3 kept for purging; what they do not reach waits for the
  next session start.
- `sweep_worktrees.py --clear <path> [--promoted <ref>]` trashes one linked bundle as the
  strategy says, never deleting it outright (refusing `no strategy saved`, `not a bundle`,
  `live`, `unlinked`, `kept by strategy` and an unpromoted reference bundle). Trash lives in
  `<git dir>/lumberjack-trash/<UTC timestamp>/`.
  With a strategy saved, session start and the post-merge sweep delete the folders past `trash_days`,
  oldest first, printing `purged <timestamp>` for each; the deleting shares the run's doc
  budget and goes bottom up, so a folder too large for it is left partly deleted (keeping its name
  and age) and a later session start finishes it. `cleanup = "report"` prints
  `trash <timestamp>: would purge <N> files (<size>)` and deletes nothing; `"keep"` neither purges nor prints. A folder
  that cannot be deleted prints `blocked trash <timestamp>: <reason>` and counts in `blocked N`.
  `--purge-trash` deletes everything past retention in one run, without a budget, and exits 1
  when any folder is blocked.
- `doc_strategy.roots` (optional): doc roots mapped to `keep`, so
  `roots = {"docs/handoffs" = "keep"}` keeps one root's bundles while others are trashed. `--clear` also
  refuses a bundle whose path or files lead outside the repository through a symlink or junction
  (`links outside the repository`), a markdown-scope bundle with an entry that resolves anywhere else,
  even inside the repository (`links outside the bundle`), and a markdown-scope bundle with no `.md` file
  (`no Markdown in scope`) instead of reporting success.
- `--clear` takes several paths (one line each, one trash folder, exit 1 when any is blocked) and,
  with `--promoted`, fetches `origin` first so a promotion merged during the session counts.
- `--json` gives each doc its resolved `action` (`trash` for ephemeral, `promote` for reference,
  `keep` under a keep-root; empty until a strategy is saved), which `--clear` and
  `/lumberjack:cleanup` follow.
- A doc root can be durable: `doc_reference` takes `"*"`, which makes every bundle of the root
  `reference`, so none is trashed by rule. Setup now always asks for doc roots (even when it finds no
  candidate) and, root by root, whether each is durable (saved as `["*"]`) or working. An empty
  `doc_reference` word, which matched every bundle, is now a config error.
- A Codex worktree on a detached HEAD in `<dir>/<slug>/<repo name>` is matched to its tickets by
  its parent folder's name, not the repository name.
- Session start reminds the maintainer to re-run setup after an upgrade: when `.lumberjack.toml` was
  written by another version's setup, it prints
  `hint: .lumberjack.toml was written by lumberjack setup <old>, the plugin is <new>; re-run /lumberjack:setup`.
- The `CLAUDE.md`/`AGENTS.md` block setup writes says what session start does in the saved `cleanup`
  mode (cleans up, only reports, or leaves worktrees alone) and when the Stop gate is off.
- `cleanup = "report"` adds a `sweep would:` line after the summary that totals what `prune` would
  do: worktrees removed, cache-only orphans deleted, bundles trashed and trash folders purged, with
  their files and bytes.
- `sweep_worktrees.py --preview` prints that report-mode run for the next session start whatever
  `cleanup` says, and writes nothing (no first-seen dates, hook settings or tally); counting files
  has no time limit. Use it to see the blast radius before the first sweep. `--files` (all, or the
  named bundle paths and trash timestamps) adds one `file <path> (<size>)` line per file, and works
  with `--tally` too; a key that matches nothing prints `files: nothing named <key>`. The `lumberjack` skill tells agents to answer from the summary and use
  `--files` when asked for detail.
- `sweep_worktrees.py --tally` counts the bundles trashed and the files purged since the last
  session start and in all, and the folders and files still in the trash, with one line per bundle
  trashed this session. Trashing, purging (a cut-short purge included) and, once a `doc_strategy` is
  saved, each session start (with its session id, so a resume or compaction stays the same session)
  are appended to `<git dir>/lumberjack-tally.jsonl`; a failed write never fails a sweep.

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
