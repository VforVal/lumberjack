---
name: setup
description: Run-once interview that writes lumberjack's config, git hook and harness pinning for this repository
disable-model-invocation: true
---

# lumberjack setup

Interview the maintainer, then write the config once. The helper script does all
writing; you only gather answers. Never commit anything.

## 1. Discover

Check `python3 --version`, then `python --version`: use the first name that reports
3.11 or newer in place of `python` below. If neither does, stop and point to
https://www.python.org/downloads/ . If git is
missing, stop and point to https://git-scm.com/downloads before running the helper.

Resolve the helper at `../../scripts/setup_lumberjack.py` relative to this skill's
directory. Use its absolute path in place of `HELPER` below, quoted for the active
shell (including paths with spaces). Run from the repository root; keep the JSON:

```
python "HELPER" discover
```

## 2. Interview

One section at a time. Open each with the recommended answer taken from the JSON and
a one-line reason; the maintainer accepts with one word or corrects it. Do not show
the next section until this one is settled. If `config.source` is set (a re-run),
recommend the values already in `config` instead of the guesses below.

**A. Harnesses** (`harnesses`). Recommend `claude` when `harness.claude_md` or
`harness.claude_settings` is true, `codex` when `harness.codex_dir` is true, both when
unsure. With claude, also ask `pin_claude_settings` (default true): it writes the
plugin into `.claude/settings.json` so teammates are prompted to install it.

**B. Branch shape.**
- `ticket_prefix`: `tracker.prefix`, else `ticket_prefix_guess`, else `""`.
- `owners`: `owners_guess`, else `[]` (empty accepts any lowercase owner).
- `prototype_prefix`: `"prototype/"`.
- `primary_branch`: `origin.default_branch`, else `"main"`.

**C. Worktree roots** (`worktree_roots`). Recommend `worktree_parents` as globs scoped
with `{name}`, for example `~/worktrees/{name}-*`: a root outside the repo without
`{name}` only matches `<dir>/<name>` subdirectories. Warn that the built-in defaults scan the home directory, so
an explicit list is always written. If `worktree_parents` is empty, ask where
worktrees will live.

**D. Behaviour.** `cleanup`: recommend `"prune"` (removes landed worktrees at session
start); offer `"report"` to watch what it would remove first. `stop_gate`: recommend
`true` (the Stop hook blocks on branch and primary-checkout debt).

**E. Git hook** (`hook`). Recommend `hook_manager` when set (`husky`, `lefthook`,
`pre-commit`, `simple-git-hooks`), else `plain`; `none` skips the hook. The hook runs
cleanup after every merge. An existing `post-merge` hook is kept: a marked block is
appended to it, or it is chained when it is not a shell script. `lefthook` always gets
a `lefthook-local.yml` job, even when `lefthook.yml` has its own `post-merge` section.

**F. Evidence directory** (`evidence_dir`). Ask only when `config.evidence_dir` is
set, or a directory named `.evidence`, `evidence` or `.<name>` sits at the root and
looks like scratch or handoff output. Its contents block cleanup of a worktree.
Otherwise set `""` without asking.

**G. Doc roots** (`doc_roots`). Ask only when `config.doc_roots` is set or
`doc_root_candidates` is not empty. Recommend the candidate `path`s, each with its one-line
`reason`, and say a landed worktree with an untracked or ignored file under a doc root stays,
reason `docs`. On a re-run keep `config.doc_roots` unless the maintainer changes it. Otherwise
set `[]` without asking.

## 3. Confirm

Show one consolidated summary and wait for an explicit yes:

1. The full answers JSON. Keys: `harnesses`, `pin_claude_settings`, `ticket_prefix`,
   `owners`, `prototype_prefix`, `primary_branch`, `worktree_roots`, `cleanup`,
   `stop_gate`, `hook`, `evidence_dir`, `doc_roots`.
2. The files apply will write or edit: `.lumberjack.toml`; the hook file for `hook`;
   `.claude/settings.json` when pinning; the `CLAUDE.md` (else `AGENTS.md`) block
   (creating `AGENTS.md` when neither exists);
   `.gitignore` entries for roots inside the repository. Apply prints `wrote <file>`
   for each one it touched.
3. The git config commands: `git config core.hooksPath .githooks` (plain hook, only
   when `hooks_path` is unset) and `git config lumberjack.script <path to the
   plugin's sweep_worktrees.py>` whenever a hook is installed.

Any change requested means back to the affected section, then confirm again.

## 4. Apply

Write the answers to a temporary file and run it once:

```
python "HELPER" apply ANSWERS.json
```

Relay its output lines verbatim. On a `setup:` error, fix the named answer and run
again. Then tell the maintainer to review `git diff` and
commit it themselves.

## 5. After apply

- Relay `printed, not run:` lines for each selected harness. With Codex, say
  that trusting the hooks (`/hooks` in Codex) is a one-time step per machine.
- `gh missing or unauthenticated...`: tell them cleanup still works but decides
  "landed" from git alone; run `gh auth login` for pull-request detection.
- `note: run ... to activate the hook`: relay it; the maintainer runs that command.

## Re-running

Safe at any time. `discover` returns `config` with `source` set, and you recommend
those values in each section. Apply only rewrites `.lumberjack.toml`, the values it
owns in `.claude/settings.json`, and text between its `lumberjack` marker lines in
hooks and instructions, and it only appends missing `.gitignore` lines; everything
else in those files is left alone.
