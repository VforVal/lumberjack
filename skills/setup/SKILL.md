---
name: setup
description: Run-once interview that writes lumberjack's config, git hook and harness pinning for this repository
disable-model-invocation: true
---

# lumberjack setup

Interview the maintainer, then write the config once. The helper script does all
writing; you only gather answers. Never commit anything.

## 1. Discover

Check `python3 --version`, then `python --version`: use the first name that reports 3.11 or
newer in place of `python` below. If neither does, stop and point to https://www.python.org/downloads/ .
If git is missing, stop and point to https://git-scm.com/downloads before running the helper.

Resolve the helper at `../../scripts/setup_lumberjack.py` relative to this skill's directory;
use its absolute path as `HELPER`, quoted for the shell. Run it from any checkout and keep the
JSON. Apply writes to `repo`, the primary checkout; when `started_in` is set, say so once.

```
python "HELPER" discover
```

## 2. Interview

One section at a time. Open each with the recommended answer taken from the JSON and
a one-line reason; the maintainer accepts with one word or corrects it. Do not show
the next section until this one is settled. If `config.source` is set (a re-run),
recommend the values already in `config` instead of the guesses below.

**0. Scope** (`scope`). Ask first: `local` (just me: config in `.git/lumberjack.toml`, block
in `CLAUDE.local.md`, nothing to commit, hook `plain` or `none`) or `team` (committed files).
Recommend `local` when `owners_guess` has more than one name or on a first try; `team` once agreed.

**A. Harnesses** (`harnesses`). Recommend `claude` when `harness.claude_md` or
`harness.claude_settings` is true, `codex` when `harness.codex_dir` is true, both when
unsure. With claude and `team`, also ask `pin_claude_settings` (default true): it writes the
plugin into `.claude/settings.json` so teammates are prompted to install it. `local`: always false.
`instructions` (team): `instructions.target`; when `instructions.symlink` is true, name
`links_to`, never write it, and offer `CLAUDE.md` or `.claude/CLAUDE.md` instead.

**B. Branch shape.**
- `ticket_prefix`: `tracker.prefix`, else `ticket_prefix_guess`, else `""`.
- `owners`: `owners_guess`, else `[]` (empty accepts any lowercase owner).
- `prototype_prefix`: `"prototype/"`.
- `primary_branch`: `origin.default_branch`, else `"main"`.

**C. Worktree roots** (`worktree_roots`): only where to look for leftover folders; registered
worktrees are swept wherever they live, so confirm, do not explore. Recommend `worktree_parents`
as globs; outside the repo, a root without `{name}` only matches `<dir>/<name>`. Always write
a list: the defaults scan the home directory.

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

**G. Doc roots** (`doc_roots`). Always ask. Recommend `config.doc_roots` on a re-run, else the
`doc_root_candidates` `path`s with their `reason`s, else ask where work documents (handoffs, plans,
research, prototypes) live ("none" is `[]`). Files there keep a landed worktree, reason `docs`.

**H. Durable roots** (`doc_reference`). Ask per doc root: durable (bundles are `reference`, never
trashed by rule, only promoted or kept) or working (bundles may be trashed once their tickets land,
under a saved `doc_strategy`). Never assume: one folder name is durable here, working elsewhere.
Recommend durable when `config.doc_reference` maps the root to `["*"]` or its name holds `research`,
`prototype` or `archive`. Start from `config.doc_reference`: set a durable root to `["*"]`; for a
working one remove `"*"`, deleting the key when no word is left. Copy `config.doc_strategy` as is.

## 3. Confirm

Show one consolidated summary and wait for an explicit yes:

1. The full answers JSON. Keys: `scope`, `harnesses`, `pin_claude_settings`, `instructions`,
   `ticket_prefix`, `owners`, `prototype_prefix`, `primary_branch`, `worktree_roots`, `cleanup`,
   `stop_gate`, `hook`, `evidence_dir`, `doc_roots`, `doc_reference`, `doc_strategy`.
2. The files apply will write: team: `.lumberjack.toml`, the hook file, `.claude/settings.json`
   when pinning, the `instructions` block, `.gitignore` entries for roots inside the repository;
   local: `.git/lumberjack.toml`, `.git/hooks/post-merge`, `CLAUDE.local.md`,
   `.git/info/exclude`. Apply prints `wrote <file>` for each one it touched.
3. The git config commands: `git config core.hooksPath .githooks` (team plain hook, only when
   `hooks_path` is unset) and `git config lumberjack.script <plugin's sweep_worktrees.py>`.

Any change requested means back to the affected section, then confirm again.

## 4. Apply

Write the answers to a temporary file and run it once:

```
python "HELPER" apply ANSWERS.json
```

Relay its output lines verbatim. On a `setup:` error, fix the named answer and run
again. Team: tell the maintainer to review `git diff` and commit it themselves.

## 5. After apply

- Relay `printed, not run:` lines for each selected harness. With Codex, say
  that trusting the hooks (`/hooks` in Codex) is a one-time step per machine.
- `gh missing or unauthenticated...`: tell them cleanup still works but decides
  "landed" from git alone; run `gh auth login` for pull-request detection.
- `note: run ... to activate the hook`: relay it; the maintainer runs that command.

## Re-running

Safe at any time. `discover` returns `config` with `source` set (`.git/lumberjack.toml`
means `local`); recommend those values. Apply rewrites only the config, the values it owns in
`.claude/settings.json` and text between its marker lines, and only appends ignore lines.
