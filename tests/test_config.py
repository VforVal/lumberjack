"""Lumberjack config overrides, through the loader and the sweep entry points."""

import io
import json
import sys
from pathlib import Path

import pytest
from helpers import git, write

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sweep_worktrees  # noqa: E402

SESSION_START = '{"hook_event_name":"SessionStart","source":"startup"}'


def configure(root: Path, body: str) -> None:
    write(root, "pyproject.toml", f"[tool.lumberjack]\n{body}\n")


def make_primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trunk: str) -> Path:
    git(tmp_path, "init", "-q", "--bare", "-b", trunk, "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", "primary")
    root = tmp_path / "primary"
    git(root, "checkout", "-q", "-B", trunk)
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    git(root, "push", "-q", "origin", trunk)
    git(root, "fetch", "-q", "origin")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_primary(tmp_path, monkeypatch, "main")


def gate_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cwd: Path
) -> str:
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    code = sweep_worktrees.main(["--gate"])
    err = capsys.readouterr().err.strip()
    assert code == (2 if err else 0)
    return err


def worktree(primary: Path, branch: str) -> Path:
    path = primary.parent / "wt" / branch.replace("/", "-")
    git(primary, "worktree", "add", "-q", "-b", branch, str(path))
    return path


def report(capsys: pytest.CaptureFixture[str]) -> dict[str, list[dict[str, str]]]:
    assert sweep_worktrees.main(["--json"]) == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def test_defaults_without_file_or_table(tmp_path: Path) -> None:
    assert sweep_worktrees.load_config(tmp_path) == sweep_worktrees.DEFAULTS
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    assert sweep_worktrees.load_config(tmp_path) == sweep_worktrees.DEFAULTS


def test_table_overrides_only_the_keys_it_names(tmp_path: Path) -> None:
    configure(tmp_path, 'owners = ["me"]\nprimary_branch = "trunk"')
    cfg = sweep_worktrees.load_config(tmp_path)
    assert cfg["owners"] == ["me"] and cfg["primary_branch"] == "trunk"
    assert cfg["evidence_dir"] == sweep_worktrees.DEFAULTS["evidence_dir"]


def test_unknown_key_exits_naming_it(
    primary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(primary, 'owner = ["typo"]')
    with pytest.raises(sweep_worktrees.ConfigError, match="owner"):
        sweep_worktrees.load_config(primary)
    with pytest.raises(SystemExit) as error:
        sweep_worktrees.main(["--json"])
    assert "owner" in str(error.value.code)


@pytest.mark.parametrize(
    ("body", "message"),
    [('owners = "val"', "owners must be list"), ('doc_roots = "docs"', "doc_roots must be list"), ('doc_roots = ["../notes"]', "doc_roots must be relative"), ('doc_roots = ["/abs"]', "doc_roots must be relative"), ('doc_roots = [1]', "doc_roots must be relative"), ('doc_roots = ["./"]', "doc_roots must be relative"), ('prototype_prefix = ""', "prototype_prefix must not be empty"), ('doc_reference = {a = "x"}', "doc_reference must map"), ('doc_strategy = {scope = "bundle"}', "doc_strategy must be"), ('doc_strategy = {scope = "burn", trash_days = 7, grace_days = 1}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 0, grace_days = 1}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, grace_days = 1, roots = {"a" = "trash"}}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, grace_days = 1, roots = "a"}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, grace_days = -1}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, grace_days = "1"}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, grace_days = 1.5}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, grace_days = true}', "doc_strategy must be"), ('doc_strategy = {scope = "bundle", trash_days = 7, ephemeral = "trash"}', "doc_strategy no longer takes"), ('doc_strategy = {scope = "bundle", trash_days = 7, reference = "promote"}', "doc_strategy no longer takes"), ('doc_strategy = {scope = "bundle", trash_days = 7, tracker = "gh"}', "doc_strategy no longer takes")],
)
def test_bad_type_and_empty_prototype_prefix_are_rejected(
    tmp_path: Path, body: str, message: str
) -> None:
    configure(tmp_path, body)
    with pytest.raises(sweep_worktrees.ConfigError, match=message):
        sweep_worktrees.load_config(tmp_path)


def test_gate_reports_config_errors(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(primary, "bogus = 1")
    assert "unknown key" in gate_errors(monkeypatch, capsys, primary)


def test_malformed_toml_blocks_the_gate_but_not_the_cleanup(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(primary, "pyproject.toml", "[tool.lumberjack\n")
    assert "bad TOML" in gate_errors(monkeypatch, capsys, primary)
    assert sweep_worktrees.main(["--cleanup"]) == 0
    assert "bad TOML" in capsys.readouterr().out


def test_lumberjack_toml_wins_over_pyproject(tmp_path: Path) -> None:
    configure(tmp_path, 'owners = ["py"]')
    write(tmp_path, ".lumberjack.toml", 'owners = ["file"]\ncleanup = "report"\n')
    cfg = sweep_worktrees.load_config(tmp_path)
    assert cfg["owners"] == ["file"] and cfg["cleanup"] == "report"


def test_new_keys_default_and_validate(tmp_path: Path) -> None:
    cfg = sweep_worktrees.load_config(tmp_path)
    assert cfg["cleanup"] == "prune" and cfg["stop_gate"] is True
    for body in ('cleanup = "nuke"', 'stop_gate = "yes"'):
        write(tmp_path, ".lumberjack.toml", body)
        with pytest.raises(sweep_worktrees.ConfigError, match="cleanup|stop_gate"):
            sweep_worktrees.load_config(tmp_path)


@pytest.mark.parametrize("mode", ["prune", "report", "keep"])
def test_session_context_delivers_canonical_rules_and_active_settings_once(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    configure(primary, f'cleanup = "{mode}"\nstop_gate = false\nowners = ["me"]')
    monkeypatch.setattr("sys.stdin", io.StringIO(SESSION_START))
    assert sweep_worktrees.main(["--hook"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.count("# Worktrees, cleanup and the Stop gate") == 1
    assert "Before forcing a removal by hand, snapshot the branch" in context
    assert f'"cleanup": "{mode}"' in context
    assert '"stop_gate": false' in context
    assert '"owners": ["me"]' in context
    assert '"primary_branch": "main"' in context
    assert sum(line.startswith("sweep: cleaned") for line in context.splitlines()) == 1
    assert "name: lumberjack" not in context


@pytest.mark.parametrize(
    "key", ["hook_files", "skill_copies", "windows_may_differ", "local_settings"]
)
def test_dropped_parity_keys_are_unknown(tmp_path: Path, key: str) -> None:
    configure(tmp_path, f"{key} = []")
    with pytest.raises(sweep_worktrees.ConfigError, match=key):
        sweep_worktrees.load_config(tmp_path)


def test_a_table_validates_without_a_file() -> None:
    parse = sweep_worktrees.parse_config
    assert parse({"cleanup": "report"})["cleanup"] == "report"
    with pytest.raises(sweep_worktrees.ConfigError, match="cleanup must be") as error:
        parse({"cleanup": "bogus"}, "draft.toml")
    assert error.value.source == "draft.toml"


def test_pre_rename_table_is_not_read(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", f"[tool.{'worktree-' + 'hygiene'}]\nbogus = 1\n")
    assert sweep_worktrees.load_config(tmp_path) == sweep_worktrees.DEFAULTS


def test_gate_honours_custom_owners_and_ticket_prefix(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(primary, 'owners = ["me"]\nticket_prefix = "abc-"')
    assert gate_errors(monkeypatch, capsys, worktree(primary, "me/abc-12-thing")) == ""
    for bad in ("claude/abc-1-thing", "prototype/thing"):
        err = gate_errors(monkeypatch, capsys, worktree(primary, bad))
        assert f"branch {bad} violates the lumberjack skill naming" in err
        assert "<owner>/abc-<n>-<slug>" in err


def test_empty_ticket_prefix_drops_the_ticket_segment() -> None:
    cfg = {**sweep_worktrees.DEFAULTS, "ticket_prefix": ""}
    pattern = sweep_worktrees.branch_pattern(cfg)
    assert pattern.match("claude/some-slug")
    assert pattern.match("prototype/some-slug")
    assert not pattern.match("prototype/")
    assert not pattern.match("claude/")


def test_empty_owners_accepts_any_owner_but_not_bad_shapes() -> None:
    pattern = sweep_worktrees.branch_pattern(sweep_worktrees.DEFAULTS)
    assert sweep_worktrees.DEFAULTS["owners"] == []
    assert pattern.match("anyone/some-slug")
    assert not pattern.match("Feature_X")
    assert not pattern.match("Anyone/some-slug")


def test_custom_prototype_prefix(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(primary, 'prototype_prefix = "spike/"\nowners = ["me"]\nticket_prefix = "eng-"')
    git(primary, "branch", "spike/eng-7-demo", "main")
    git(primary, "branch", "prototype/eng-8-old", "main")
    data = report(capsys)
    assert data["prototypes"] == [{"issue": "eng-7", "name": "spike/eng-7-demo"}]
    assert data["name_violations"] == ["prototype/eng-8-old"]


def test_primary_branch_is_the_base_and_the_resting_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_primary(tmp_path, monkeypatch, "trunk")
    configure(root, 'primary_branch = "trunk"')
    write(root, ".git/info/exclude", "pyproject.toml\n")  # keep it clean
    assert gate_errors(monkeypatch, capsys, root) == ""
    assert sweep_worktrees.main(["--cleanup"]) == 0
    assert capsys.readouterr().out.startswith("sweep: cleaned 0, landed 0")


def evidence_worktree(primary: Path) -> Path:
    write(primary, ".git/info/exclude", ".evidence/\n")  # keeps the worktree clean
    path = worktree(primary, "claude/eng-9-done")
    write(path, "done.txt", "x\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "done")
    git(primary, "merge", "-q", "--ff-only", "claude/eng-9-done")
    git(primary, "push", "-q", "origin", "main")
    git(primary, "fetch", "-q", "origin")
    git(path, "push", "-q", "-u", "origin", "claude/eng-9-done")
    write(path, ".evidence/note.md", "keep\n")
    return path


def test_session_context_reports_invalid_config_without_cleanup_or_default_settings(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = evidence_worktree(primary)
    write(primary, ".lumberjack.toml", 'cleanup = "invalid"\n')
    monkeypatch.setattr("sys.stdin", io.StringIO(SESSION_START))
    assert sweep_worktrees.main(["--hook"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "cleanup must be one of prune, report, keep" in context
    assert "Active lumberjack settings" not in context
    assert "# Worktrees, cleanup and the Stop gate" in context
    assert path.exists()


def test_session_context_reads_primary_config_from_linked_worktree(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = worktree(primary, "me/task")
    configure(primary, 'cleanup = "keep"\nstop_gate = false')
    configure(path, 'cleanup = "prune"\nstop_gate = true')
    monkeypatch.chdir(path)
    monkeypatch.setattr("sys.stdin", io.StringIO(SESSION_START))
    assert sweep_worktrees.main(["--hook"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert '"cleanup": "keep"' in context
    assert '"stop_gate": false' in context
    assert '"stop_gate": true' not in context


@pytest.mark.parametrize("payload", [
    '{"hook_event_name":"SubagentStart","agent_type":"general-purpose"}',
    '{"hook_event_name":"SubagentStart","agent_type":"default","turn_id":"1"}',
    '{"hook_event_name":"SubagentStart","agent_type":"fork","turn_id":"1"}',
])
def test_subagent_context_delivers_active_rules_without_cleanup_self_heal_or_fetch(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: str
) -> None:
    path = evidence_worktree(primary)
    configure(primary, 'cleanup = "prune"\nstop_gate = false')
    fetched_at = (primary / ".git" / "FETCH_HEAD").stat().st_mtime_ns
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert sweep_worktrees.main(["--hook"]) == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["hookEventName"] == "SubagentStart"
    assert output["additionalContext"].count("# Worktrees, cleanup and the Stop gate") == 1
    assert '"cleanup": "prune"' in output["additionalContext"]
    assert '"stop_gate": false' in output["additionalContext"]
    assert not any(line.startswith("sweep:") for line in output["additionalContext"].splitlines())
    assert path.exists()
    assert "lumberjack.script=" not in git(primary, "config", "--list")
    assert (primary / ".git" / "FETCH_HEAD").stat().st_mtime_ns == fetched_at


def test_claude_fork_keeps_inherited_context_without_injecting_again(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"hook_event_name":"SubagentStart","agent_type":"fork"}'
    ))
    assert sweep_worktrees.main(["--hook"]) == 0
    assert capsys.readouterr() == ("", "")
    assert "lumberjack.script=" not in git(primary, "config", "--list")


def test_subagent_config_error_reports_no_active_defaults(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(primary, ".lumberjack.toml", 'stop_gate = "wrong"')
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"hook_event_name":"SubagentStart","agent_type":"custom-reviewer"}'
    ))
    assert sweep_worktrees.main(["--hook"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "stop_gate must be bool" in context
    assert "Active lumberjack settings" not in context
    assert "# Worktrees, cleanup and the Stop gate" in context


@pytest.mark.parametrize("payload", ["", "{", "[]", "{}", '{"hook_event_name":"PreToolUse"}'])
def test_hook_rejects_input_without_a_handled_event_and_changes_nothing(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: str
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert sweep_worktrees.main(["--hook"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "requires SessionStart, SubagentStart or Stop hook input" in output.err
    assert "lumberjack.script=" not in git(primary, "config", "--list")


def test_custom_evidence_dir_protects_worktree_from_cleanup(primary: Path) -> None:
    path = evidence_worktree(primary)
    configure(primary, 'evidence_dir = ".evidence"')
    assert sweep_worktrees.main(["--cleanup"]) == 0
    assert path.exists()
    (primary / "pyproject.toml").unlink()
    assert sweep_worktrees.main(["--cleanup"]) == 0
    assert not path.exists()


def test_empty_evidence_dir_protects_nothing_extra(primary: Path) -> None:
    path = evidence_worktree(primary)
    assert sweep_worktrees.DEFAULTS["evidence_dir"] == ""
    assert sweep_worktrees.main(["--cleanup"]) == 0
    assert not path.exists()


def test_session_start_keeps_a_worktree_with_a_file_under_a_doc_root(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = evidence_worktree(primary)
    write(path, "docs/work/plan.md", "keep\n")
    write(primary, ".git/info/exclude", ".evidence/\ndocs/work/\n")
    configure(primary, 'doc_roots = ["docs/work"]')
    monkeypatch.setattr("sys.stdin", io.StringIO(SESSION_START))
    assert sweep_worktrees.main(["--hook"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "blocked claude/eng-9-done wt/claude-eng-9-done: docs" in context
    assert path.exists()


def orphan_paths(capsys: pytest.CaptureFixture[str]) -> set[Path]:
    return {Path(o["path"]) for o in report(capsys)["orphans"]}


def test_custom_worktree_roots_are_scanned(
    primary: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    (primary / "rel/a").mkdir(parents=True)
    (home / "trees/primary/b").mkdir(parents=True)
    (home / "glob/c/primary").mkdir(parents=True)  # nested <name> dir wins
    (home / "glob/d").mkdir()  # shared root: may belong to another repo
    (home / "other/e").mkdir(parents=True)  # not a configured root
    (home / "sib/primary-f").mkdir(parents=True)
    (home / "sib/elsewhere-g").mkdir()
    (home / "plain/h").mkdir(parents=True)  # shared, not starred
    (home / "plain/i/primary").mkdir(parents=True)
    (primary.parent / "up/j").mkdir(parents=True)  # ../ leaves the repo: shared
    (primary.parent / "up/k/primary").mkdir(parents=True)
    roots = ["rel", "~/trees/{name}", "~/glob/*", "~/sib/{name}-*", "~/plain", "../up"]
    configure(primary, f"worktree_roots = {json.dumps(roots)}")
    assert orphan_paths(capsys) == {
        primary / "rel/a",
        home / "trees/primary/b",
        home / "glob/c/primary",
        home / "sib/primary-f",
        home / "plain/i/primary",
        primary.parent / "up/k/primary",
    }


def test_default_roots_are_harness_conventions(
    primary: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    (home / ".codex/worktrees/x/primary").mkdir(parents=True)
    (home / ".codex/worktrees/y").mkdir()  # another repo's Codex worktree
    (primary / ".claude/worktrees/z").mkdir(parents=True)
    (home / "worktrees/primary/w").mkdir(parents=True)
    assert orphan_paths(capsys) == {
        home / ".codex/worktrees/x/primary",
        primary / ".claude/worktrees/z",
        home / "worktrees/primary/w",
    }
