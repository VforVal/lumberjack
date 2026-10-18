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
- The sweep lists the bundles (outermost untracked entries) of the primary checkout's
  `doc_roots` with their tickets, class (`ephemeral` or `reference`, set per root by the new
  `doc_reference` setting) and live, linked or unlinked state. The summary line gains
  `docs N` and each linked bundle prints `doc <class> <path>: tickets <ids>; <size>`.
  Nothing is deleted.
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
  bundles (scope, action per class, trash days, tracker) and saves with the new
  `setup_lumberjack.py strategy <file.json>`; a re-run of setup keeps it. Until it is saved nothing
  is removed. The command checks the tracker with the agent's own tools, offers a bundle only when
  every listed ticket is closed and the bundle is not live, asks once per group, and offers
  `reference` bundles only promote or keep.
- `sweep_worktrees.py --clear <path> [--promoted <ref>]` trashes or deletes one linked bundle as the
  strategy says (refusing `no strategy saved`, `not a bundle`, `live`, `unlinked`, `kept by strategy`
  and an unpromoted reference bundle). Trash lives in `<git dir>/lumberjack-trash/<UTC timestamp>/`.
  The sweep prints a `trash <timestamp>: N days old; <size>` line (and `, trash N` in the summary)
  for folders past `trash_days`; only `--purge-trash` deletes them.
- `doc_strategy.roots` (optional): an action per doc root that overrides the class action, so
  `roots = {"docs/handoffs" = "keep"}` keeps one root's bundles while others are trashed. `--clear` also
  refuses a bundle whose path or files lead outside the repository through a symlink or junction
  (`links outside the repository`) and a markdown-scope bundle with no `.md` file
  (`no Markdown in scope`) instead of reporting success.

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
