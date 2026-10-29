# lumberjack

Claude Code and Codex plugin repository: worktree sweep and Stop hook gate.
Users start at `README.md`; terms are in `GLOSSARY.md`.

## Layout

- `scripts/sweep_worktrees.py` - worktree and branch sweep (standard-library Python 3.11+).
- `scripts/setup_lumberjack.py` - setup helper: `discover` prints repo facts as JSON (read-only),
  `apply ANSWERS.json` writes `.lumberjack.toml`, the post-merge hook (marker-bounded block),
  Claude settings, the CLAUDE.md/AGENTS.md block and `.gitignore` entries; never commits.
  Imports `sweep_worktrees` for config loading and `parse_config`, remote URL parsing, the hook
  markers (`HOOK_BEGIN`, `HOOK_END`, `HOOK_MARKERS`) and `hook_manager` (`HOOK_MANAGERS` table).
- `skills/lumberjack/SKILL.md` - the rules the script enforces.
- `skills/cleanup/SKILL.md` - `/lumberjack:cleanup` (user-invoked only): runs `--cleanup`, then
  removes `unverified` and `landed-probable` worktrees one confirmed `--remove <path>` at a time.
- `skills/setup/SKILL.md` - run-once interview (`/lumberjack:setup`, user-invoked only) that drives
  `discover` and `apply`; its questions follow their JSON keys.
- `hooks/hooks.json` - shared hooks: SessionStart, SubagentStart and Stop all run the sweep's
  `--hook`, which reads the hook input on stdin and dispatches on `hook_event_name`.
- `.claude-plugin/`, `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` -
  Claude Code and Codex manifests and marketplace listings.
- `VERSION` - keep it equal to both `plugin.json` files, `pyproject.toml` and the two
  version mentions in `website/dist/index.html`.
- `README.md` - the stranger-facing guide; its config table, tier and reason names, summary
  line and setup output lines must match the scripts verbatim.
- `CHANGELOG.md` - one section per released `VERSION`.
- `.github/workflows/release.yml` - releases: a push to main that changes `VERSION` checks the
  other version files match, then tags `v<VERSION>` and publishes a GitHub release whose notes
  are the `## <VERSION>` section of `CHANGELOG.md`. A release PR only bumps the files and
  renames `## Unreleased`; never tag by hand.
- `website/` - the static product site (`website/dist`, no build step), deployed to GitHub Pages
  by `.github/workflows/site.yml`; see `website/README.md`. Its demo quotes the sweep's and
  gate's output lines and the install commands verbatim, and shows the version twice.
- `GLOSSARY.md` - the sweep's vocabulary (integrated, landed, probable, unverified, dependent, fresh, locked, cleanup).
- `SECURITY.md` - private vulnerability reporting and what counts as a security issue.
- `tests/` - pytest suite, one module per area: `test_sweep_worktrees.py`, `test_config.py`,
  `test_setup_discover.py`, `test_setup_apply.py`, `test_lifecycle.py`, `test_rule_delivery.py`;
  `conftest.py` isolates HOME, git identity and `shutil.which`, `helpers.py` holds `git` and
  `write`. Main seams: each script's `main()` against temp git repos, a `gh` shim on PATH, and
  a real merge through the Python hook. A few tests call `classify`, `short_path`,
  `load_config`, `parse_config`, `block_reason` and `manager_wired` directly and patch
  `orphan_candidates`, `GH_TIMEOUT` and `gh_facts`.

## Tests

`python -m pip install "pytest>=8"`, then `python -m pytest -q`.

## Rules

- Scripts stay standard-library only and run on Python 3.11.
- Keep LF line endings.
- `skills/lumberjack/SKILL.md` stays at most 79 lines, `skills/setup/SKILL.md` at most 120;
  they and `skills/cleanup/SKILL.md` name no consumer project, path or ticket; config keys
  carry the project-specific values.

<!-- >>> lumberjack -->
## Lumberjack

Worktree rules live in the `lumberjack` skill; config in `.lumberjack.toml`.
Session start cleans up landed worktrees; the Stop hook gates debt.
Re-run the `lumberjack:setup` skill after conventions change.
<!-- <<< lumberjack -->
