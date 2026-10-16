# Security policy

lumberjack runs from agent hooks and a git post-merge hook. It removes worktrees and
writes files in your repository, so bugs in those paths count as security issues.

## Supported versions

Only the latest release gets fixes.

## Reporting a vulnerability

Report privately through GitHub: the repository's **Security** tab, then
**Report a vulnerability**. Do not open a public issue for a vulnerability.

Include the lumberjack version, your OS, the harness (Claude Code or Codex) and the
steps that reproduce the problem.

## In scope

- Cleanup that removes a worktree holding uncommitted, untracked or unpushed
  work.
- Setup writing outside the repository, other than a `core.hooksPath` you configured.
- Command injection through branch names, paths or configuration values.
