"""Setup and sweep together, with disposable roots and restorable worktrees."""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git, write

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_lumberjack  # noqa: E402
import sweep_worktrees  # noqa: E402


@pytest.mark.parametrize("entrypoint", ["setup", "cleanup"])
@pytest.mark.parametrize("source", ["worktree", "external", "nested"])
def test_hook_script_survives_source_worktree_removal(
    configured_repo: tuple[Path, Path], tmp_path: Path, entrypoint: str, source: str,
) -> None:
    primary, trees = configured_repo
    project = Path(sweep_worktrees.__file__).resolve().parents[1]
    for rel in ("scripts/setup_lumberjack.py", "scripts/sweep_worktrees.py",
                "skills/lumberjack/SKILL.md", "VERSION", ".gitignore"):
        write(primary, rel, (project / rel).read_text(encoding="utf-8"))
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "include plugin sources")
    worktree = trees / "plugin source"
    git(primary, "worktree", "add", "-q", "-b", "codex/eng-3-source", str(worktree))
    plugin = worktree
    if source != "worktree":
        plugin = (primary if source == "nested" else tmp_path) / "installed plugin"
        shutil.copytree(worktree / "scripts", plugin / "scripts")
        shutil.copytree(worktree / "skills", plugin / "skills")
        shutil.copyfile(worktree / "VERSION", plugin / "VERSION")
    if entrypoint == "setup":
        answers = tmp_path / "setup answers.json"
        data = json.loads(answers.read_text(encoding="utf-8"))
        data.update(hook="plain", cleanup="report")
        answers.write_text(json.dumps(data), encoding="utf-8")
        args = [str(plugin / "scripts/setup_lumberjack.py"), "apply", str(answers),
                "--repo", str(primary)]
    else:
        args = [str(plugin / "scripts/sweep_worktrees.py"), "--cleanup"]
    done = subprocess.run([sys.executable, *args], cwd=primary, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    script = Path(git(primary, "config", "--get", "lumberjack.script").strip())
    expected = primary if source == "worktree" else plugin
    assert script == expected / "scripts/sweep_worktrees.py"
    git(primary, "worktree", "remove", str(worktree))
    done = subprocess.run(
        [sys.executable, str(script), "--gate"], cwd=primary, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture
def configured_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, Path]:
    """All scanned paths stay under this test's root, including on Windows."""
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", "primary checkout")
    primary = tmp_path / "primary checkout"
    trees = tmp_path / "primary checkout worktrees"
    git(primary, "commit", "-q", "--allow-empty", "-m", "root")
    git(primary, "push", "-q", "origin", "main")
    answers = {
        "harnesses": ["claude", "codex"],
        "owners": ["codex"],
        "ticket_prefix": "eng-",
        "prototype_prefix": "prototype/",
        "primary_branch": "main",
        "evidence_dir": ".evidence",
        "worktree_roots": [(tmp_path / "{name} worktrees").as_posix()],  # {name}: not shared
        "cleanup": "prune",
        "stop_gate": True,
        "hook": "none",
        "pin_claude_settings": False,
    }
    answer_path = tmp_path / "setup answers.json"
    write(tmp_path, answer_path.name, json.dumps(answers))
    assert setup_lumberjack.main(["apply", str(answer_path), "--repo", str(primary)]) == 0
    capsys.readouterr()
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "setup")
    git(primary, "push", "-q", "origin", "main")
    write(primary, ".git/info/exclude", ".evidence/\n")
    return primary, trees


def landed(primary: Path, trees: Path, name: str, *, pushed: bool = True) -> Path:
    path = trees / name
    branch = f"codex/eng-1-{name}"
    git(primary, "worktree", "add", "-q", "-b", branch, str(path))
    write(path, "payload.txt", name + "\n")
    git(path, "add", "payload.txt")
    git(path, "commit", "-q", "-m", name)
    git(primary, "merge", "-q", "--ff-only", branch)
    git(primary, "push", "-q", "origin", "main")
    if pushed:
        git(path, "push", "-q", "-u", "origin", branch)
    return path


@pytest.mark.parametrize("mode", ["prune", "report", "keep"])
def test_setup_cleanup_modes_protect_and_restore_worktrees(
    configured_repo: tuple[Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    primary, trees = configured_repo
    config = primary / ".lumberjack.toml"
    config.write_text(config.read_text().replace('cleanup = "prune"', f'cleanup = "{mode}"'))
    clean = landed(primary, trees, "clean")
    clean_tip = git(clean, "rev-parse", "HEAD").strip()
    dirty = landed(primary, trees, "dirty")
    write(dirty, "scratch.txt", "keep this uncommitted work\n")
    evidence = landed(primary, trees, "evidence")
    write(evidence, ".evidence/receipt.txt", "keep this receipt\n")
    unpushed = landed(primary, trees, "unpushed", pushed=False)
    prototype = landed(primary, trees, "prototype")
    git(prototype, "branch", "-m", "prototype/eng-1-capture")
    git(prototype, "push", "-q", "-u", "origin", "prototype/eng-1-capture")
    write(trees, "old cache/node_modules/cache", "cache\n")
    write(tmp_path, "outside roots/node_modules/receipt", "outside scan\n")
    monkeypatch.chdir(primary)

    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name":"SessionStart","source":"startup"}'))
    assert sweep_worktrees.main(["--hook"]) == 0
    envelope = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert envelope["hookEventName"] == "SessionStart"
    context = envelope["additionalContext"]
    assert '# Worktrees, cleanup and the Stop gate' in context
    assert f'"cleanup": "{mode}"' in context
    assert '"stop_gate": true' in context
    output = context.split("\n\n# Worktrees, cleanup and the Stop gate", 1)[0]
    assert all(path.exists() for path in (dirty, evidence, unpushed, prototype))
    assert (dirty / "scratch.txt").read_text() == "keep this uncommitted work\n"
    assert (evidence / ".evidence/receipt.txt").read_text() == "keep this receipt\n"
    assert (tmp_path / "outside roots/node_modules/receipt").exists()
    if mode == "prune":
        assert not clean.exists() and not (trees / "old cache").exists()
        assert "(local branch kept)" in output
        restored = trees / "restored checkout"
        git(primary, "worktree", "add", "-q", str(restored), "codex/eng-1-clean")
        assert git(restored, "rev-parse", "HEAD").strip() == clean_tip
        assert (restored / "payload.txt").read_text() == "clean\n"
    else:
        assert clean.exists() and (trees / "old cache").exists()
        assert "cleaned codex/" not in output
        assert ("would remove" in output) is (mode == "report")


def test_session_start_hook_trashes_a_linked_bundle(
    configured_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    primary, _ = configured_repo
    config = primary / ".lumberjack.toml"
    config.write_text(config.read_text().replace("doc_roots = []", 'doc_roots = ["docs/work"]'))
    write(primary, "strategy.json", '{"scope": "bundle", "trash_days": 30, "grace_days": 0}')
    assert setup_lumberjack.main(["strategy", str(primary / "strategy.json"), "--repo", str(primary)]) == 0
    capsys.readouterr()
    write(primary, "docs/work/eng-9-handoff/notes.md", "done\n")
    monkeypatch.chdir(primary)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name":"SessionStart","source":"startup"}'))
    assert sweep_worktrees.main(["--hook"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    (folder,) = (primary / ".git" / "lumberjack-trash").iterdir()
    assert f"cleared docs/work/eng-9-handoff: trashed {folder.name}" in context
    assert ", docs 1" in context
    assert not (primary / "docs/work/eng-9-handoff").exists()
    assert (folder / "docs/work/eng-9-handoff/notes.md").read_text() == "done\n"


@pytest.mark.parametrize("json_output", [False, True])
def test_setup_stop_gate_rejects_once_and_respects_disabled_setting(
    configured_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], json_output: bool,
) -> None:
    """`--hook` answers the harness Stop event with JSON; plain `--gate` keeps exit 2."""
    primary, _ = configured_repo
    args = ["--hook"] if json_output else ["--gate"]
    git(primary, "switch", "-q", "-c", "Feature_X")
    monkeypatch.chdir(primary)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name":"Stop"}'))
    assert sweep_worktrees.main(args) == (0 if json_output else 2)
    captured = capsys.readouterr()
    if json_output:
        decision = json.loads(captured.out)
        assert decision["decision"] == "block" and captured.err == ""
        assert "branch Feature_X violates" in decision["reason"]
    else:
        assert "branch Feature_X violates" in captured.err and captured.out == ""
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name":"Stop","stop_hook_active":true}'))
    assert sweep_worktrees.main(args) == 0
    assert capsys.readouterr() == ("", "")
    config = primary / ".lumberjack.toml"
    config.write_text(config.read_text().replace("stop_gate = true", "stop_gate = false"))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name":"Stop"}'))
    assert sweep_worktrees.main(args) == 0
    assert capsys.readouterr() == ("", "")
    config.write_text('stop_gate = "wrong"\n')
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name":"Stop"}'))
    assert sweep_worktrees.main(args) == (0 if json_output else 2)
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out) == {
            "decision": "block", "reason": "lumberjack: stop_gate must be bool",
        }
        assert captured.err == ""
    else:
        assert "stop_gate must be bool" in captured.err and captured.out == ""


@pytest.mark.skipif(
    REAL_WHICH("pwsh") is None and REAL_WHICH("powershell") is None,
    reason="PowerShell is not installed",
)
def test_json_gate_blocks_through_a_real_powershell_process(
    configured_repo: tuple[Path, Path],
) -> None:
    primary, _ = configured_repo
    git(primary, "switch", "-q", "-c", "Feature_X")
    root = Path(sweep_worktrees.__file__).resolve().parents[1]
    hooks = json.loads((root / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    command = hooks["Stop"][0]["hooks"][0]["command"].replace("${CLAUDE_PLUGIN_ROOT}", root.as_posix())
    done = subprocess.run(
        [REAL_WHICH("pwsh") or REAL_WHICH("powershell"), "-NoLogo", "-NoProfile", "-Command",
         f"@{{hook_event_name='Stop'}} | ConvertTo-Json -Compress | {command}"],
        cwd=primary, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    decision = json.loads(done.stdout)
    assert decision["decision"] == "block"
    assert "branch Feature_X violates" in decision["reason"]
    assert done.stderr == ""


def test_real_post_merge_hook_prunes_under_paths_with_spaces(
    configured_repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, trees = configured_repo
    monkeypatch.setattr("shutil.which", REAL_WHICH)
    clean = landed(primary, trees, "hook")
    answer_path = primary.parent / "setup answers.json"
    answers = json.loads(answer_path.read_text())
    answers["hook"] = "plain"
    answer_path.write_text(json.dumps(answers))
    assert setup_lumberjack.main(["apply", str(answer_path), "--repo", str(primary)]) == 0
    capsys.readouterr()
    git(primary, "add", ".githooks/post-merge")
    git(primary, "update-index", "--chmod=+x", ".githooks/post-merge")
    git(primary, "commit", "-q", "-m", "hook")
    git(primary, "push", "-q", "origin", "main")
    git(primary, "branch", "codex/eng-2-trigger", "main")
    git(primary, "switch", "-q", "codex/eng-2-trigger")
    git(primary, "commit", "-q", "--allow-empty", "-m", "trigger hook")
    git(primary, "switch", "-q", "main")

    done = subprocess.run(
        ["git", "merge", "--no-ff", "-m", "merge trigger", "codex/eng-2-trigger"],
        cwd=primary, capture_output=True, text=True, check=True,
    )
    assert "cleaned codex/eng-1-hook" in done.stdout + done.stderr
    assert not clean.exists()
    assert git(primary, "rev-parse", "--verify", "codex/eng-1-hook").strip()


@pytest.mark.parametrize("script", ["sweep_worktrees.py", "setup_lumberjack.py"])
def test_scripts_refuse_old_python_with_a_plain_message(script: str) -> None:
    path = Path(sweep_worktrees.__file__).with_name(script)
    done = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.version_info = (3, 10, 0); exec(open({str(path)!r}).read())"],
        capture_output=True, text=True,
    )
    assert done.returncode == 1 and "Traceback" not in done.stderr
    assert done.stderr.strip() == f"lumberjack: Python 3.11+ required, found {sys.version.split()[0]}"
