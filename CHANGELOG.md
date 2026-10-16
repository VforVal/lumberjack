# Changelog

## Unreleased

- `commit_email` config key: a regex, empty by default. When set, the Stop gate blocks
  (`gate: commit email:`) when `git config user.email` or the author or committer email
  of a commit not yet on `origin` does not match it.
- `/lumberjack:setup`: `discover` reports whether `origin` is a public GitHub repository
  and the account's noreply address; the interview recommends a noreply pattern for
  public repositories; `apply` prints, but never runs, the `git config user.email`
  command when the current value does not match, and reminds the maintainer of GitHub's
  email privacy settings.

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
