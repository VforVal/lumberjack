"""The shared plugin lifecycle registration delivers rules and sweeps once per event."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git, write

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sweep_worktrees  # noqa: E402

ANTIGRAVITY_MANIFEST_KEYS = {
    "name",
    "displayName",
    "version",
    "description",
    "logo",
    "author",
    "homepage",
    "repository",
    "suggestedPrompts",
    "disabled",
}


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_each_session_source_has_one_combined_delivery_and_cleanup(source: str) -> None:
    root = Path(__file__).resolve().parents[1]
    hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    matched = [
        hook
        for group in hooks["SessionStart"]
        if re.search(group.get("matcher", ".*"), source)
        for hook in group["hooks"]
    ]
    assert len(matched) == 1
    assert matched[0]["command"].endswith("--hook")
    assert len(hooks["Stop"]) == 1
    assert hooks["Stop"][0]["hooks"][0]["command"].endswith("--hook")


def test_subagent_has_one_context_only_hook() -> None:
    root = Path(__file__).resolve().parents[1]
    hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert len(hooks["SubagentStart"]) == 1
    assert len(hooks["SubagentStart"][0]["hooks"]) == 1
    command = hooks["SubagentStart"][0]["hooks"][0]["command"]
    assert command.endswith("--hook")


def registered_hook(tmp_path: Path, event: str, *shims: str) -> subprocess.CompletedProcess[str]:
    """Run the registered hook command through the host shell; shims lead PATH."""
    root = Path(__file__).resolve().parents[1]
    plugin = tmp_path / "plugin with spaces"
    shutil.copytree(root / "scripts", plugin / "scripts")
    shutil.copytree(root / "skills", plugin / "skills")
    repo = tmp_path / "repo with spaces"
    git(tmp_path, "init", "-q", "-b", "Feature_X", str(repo))
    git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    write(repo, ".lumberjack.toml", 'cleanup = "report"\nworktree_roots = []\n')
    hooks = json.loads((root / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    command = hooks[event][0]["hooks"][0]["command"].replace(
        "${CLAUDE_PLUGIN_ROOT}", plugin.as_posix(),
    )
    # No uv on PATH: the command must run on plain Python.
    uv = REAL_WHICH("uv")
    path = os.pathsep.join(
        [*shims] + [part for part in os.environ["PATH"].split(os.pathsep)
                    if not uv or Path(part).resolve() != Path(uv).parent.resolve()]
    )
    shell = REAL_WHICH("sh") or git(repo, "var", "GIT_SHELL_PATH").strip()
    return subprocess.run(
        [shell, "-c", command], cwd=repo, input=json.dumps({"hook_event_name": event}),
        capture_output=True, text=True, env={**os.environ, "PATH": path},
    )


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart", "Stop"])
def test_registered_hook_runs_through_the_host_shell_without_uv(
    tmp_path: Path, event: str,
) -> None:
    done = registered_hook(tmp_path, event)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    output = json.loads(done.stdout)
    if event == "Stop":
        assert output["decision"] == "block"
        assert "branch Feature_X violates" in output["reason"]
    else:
        assert "Worktree" in output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("stale", ["python3", "python"])
def test_registered_hook_skips_a_python_older_than_311(tmp_path: Path, stale: str) -> None:
    # Stock macOS and Ubuntu 22.04 ship an old python3; CentOS 7 has python 2. The launcher
    # must pick the other name by version, not by position.
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / stale).write_text("#!/bin/sh\necho Python 3.9.6\n", encoding="utf-8")
    (shims / ("python" if stale == "python3" else "python3")).write_text(
        f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8",
    )
    for shim in shims.iterdir():
        shim.chmod(0o755)
    done = registered_hook(tmp_path, "Stop", str(shims))
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert json.loads(done.stdout)["decision"] == "block"


def stage_antigravity_plugin(plugin: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "scripts", plugin / "scripts", dirs_exist_ok=True)
    shutil.copytree(root / "skills", plugin / "skills", dirs_exist_ok=True)
    for name in ("plugin.json", "hooks.json"):
        shutil.copyfile(root / name, plugin / name)


def antigravity_hook(
    plugin: Path,
    payload: dict[str, object],
    event: str,
    *shims: str,
    use_sh: bool = False,
) -> subprocess.CompletedProcess[str]:
    stage_antigravity_plugin(plugin)
    hooks = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))["lumberjack"]
    command = hooks[event][0]["command"]
    uv = REAL_WHICH("uv")
    path = os.pathsep.join(
        [*shims]
        + [
            part
            for part in os.environ["PATH"].split(os.pathsep)
            if not uv or Path(part).resolve() != Path(uv).parent.resolve()
        ]
    )
    if os.name == "nt" and not use_sh:
        argv = ["cmd", "/c", command]
    else:
        shell = REAL_WHICH("sh") or git(Path(__file__).resolve().parents[1], "var", "GIT_SHELL_PATH").strip()
        argv = [shell, "-c", command]
    return subprocess.run(
        argv,
        cwd=plugin,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path},
    )


def test_antigravity_manifest_and_hooks_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "lumberjack"
    assert manifest["version"] == (root / "VERSION").read_text(encoding="utf-8").strip()
    assert set(manifest) <= ANTIGRAVITY_MANIFEST_KEYS
    assert (root / manifest["logo"]).is_file()

    hooks = json.loads((root / "hooks.json").read_text(encoding="utf-8"))["lumberjack"]
    expected_cmd = "git --git-dir=. -c alias.lumberjack=!sh lumberjack scripts/hook.sh"
    assert hooks["SessionStart"] == [{"type": "command", "command": expected_cmd, "timeout": 30}]
    assert hooks["Stop"] == [{"type": "command", "command": expected_cmd, "timeout": 15}]


@pytest.mark.parametrize("layout", ["global", "workspace"])
def test_antigravity_registered_hook_runs_in_host_shell(tmp_path: Path, layout: str) -> None:
    repo = tmp_path / "repo with spaces"
    git(tmp_path, "init", "-q", "-b", "Feature_X", str(repo))
    git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    write(repo, ".lumberjack.toml", 'cleanup = "report"\nworktree_roots = []\n')

    plugin = (
        tmp_path / "plugin with spaces"
        if layout == "global"
        else repo / ".agents" / "plugins" / "lumberjack"
    )

    done = antigravity_hook(
        plugin,
        {"hookEventName": "SessionStart", "conversationId": "conv-1", "cwd": str(repo), "workspacePaths": [str(repo)]},
        "SessionStart",
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    msg = json.loads(done.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "Worktree" in msg and "Active lumberjack settings" in msg

    unconfigured = tmp_path / "unconfigured repo"
    git(tmp_path, "init", "-q", "-b", "main", str(unconfigured))
    git(unconfigured, "commit", "-q", "--allow-empty", "-m", "root")
    done_unconf = antigravity_hook(
        plugin,
        {"hookEventName": "SessionStart", "workspacePaths": [str(unconfigured)]},
        "SessionStart",
    )
    assert done_unconf.returncode == 0, done_unconf.stderr
    assert json.loads(done_unconf.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"] == sweep_worktrees.NOT_SET_UP

    plain_dir = tmp_path / "plain folder"
    plain_dir.mkdir(exist_ok=True)
    for empty_payload in (
        {"hookEventName": "SessionStart", "cwd": str(plain_dir), "workspacePaths": [str(plain_dir)]},
        {"hookEventName": "SessionStart", "cwd": "", "workspacePaths": []},
    ):
        done_silent = antigravity_hook(plugin, empty_payload, "SessionStart")
        assert done_silent.returncode == 0, done_silent.stderr
        assert json.loads(done_silent.stdout) == {}

    done_stop = antigravity_hook(
        plugin,
        {
            "hookEventName": "Stop",
            "terminationReason": "NO_TOOL_CALL",
            "continuationActive": False,
            "cwd": str(repo),
            "workspacePaths": [str(repo)],
        },
        "Stop",
    )
    assert done_stop.returncode == 0, done_stop.stderr
    stop_out = json.loads(done_stop.stdout)
    assert stop_out["decision"] == "continue"
    assert "branch Feature_X violates" in stop_out["reason"]

    for skip_payload in (
        {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "continuationActive": True, "cwd": str(repo)},
        {"hookEventName": "Stop", "terminationReason": "USER_CANCELED", "continuationActive": False, "cwd": str(repo)},
    ):
        done_skip = antigravity_hook(plugin, skip_payload, "Stop")
        assert done_skip.returncode == 0, done_skip.stderr
        assert json.loads(done_skip.stdout) == {}

    git(repo, "checkout", "-q", "-b", "main")
    done_clean = antigravity_hook(
        plugin,
        {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(repo)},
        "Stop",
    )
    assert done_clean.returncode == 0, done_clean.stderr
    assert json.loads(done_clean.stdout) == {}


@pytest.mark.parametrize("stale", ["python3", "python"])
def test_antigravity_hook_skips_old_python(tmp_path: Path, stale: str) -> None:
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", "-b", "Feature_X", str(repo))
    git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    write(repo, ".lumberjack.toml", 'cleanup = "report"\nworktree_roots = []\n')

    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / stale).write_text("#!/bin/sh\necho Python 3.9.6\n", encoding="utf-8")
    (shims / ("python" if stale == "python3" else "python3")).write_text(
        f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
        encoding="utf-8",
    )
    for shim in shims.iterdir():
        shim.chmod(0o755)

    done = antigravity_hook(
        tmp_path / "plugin",
        {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(repo)},
        "Stop",
        str(shims),
        use_sh=True,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["decision"] == "continue"


def test_antigravity_session_start_records_conversation_id_in_tally(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", str(repo))
    write(
        repo,
        ".lumberjack.toml",
        'cleanup = "prune"\nworktree_roots = []\n'
        'doc_roots = ["docs/work"]\n'
        'doc_strategy = {scope = "bundle", trash_days = 7, grace_days = 0}\n',
    )
    git(repo, "add", ".lumberjack.toml")
    git(repo, "commit", "-q", "-m", "setup")
    git(repo, "push", "-q", "origin", "main")

    done = antigravity_hook(
        tmp_path / "plugin",
        {"hookEventName": "SessionStart", "conversationId": "conv-42", "cwd": str(repo)},
        "SessionStart",
    )
    assert done.returncode == 0, done.stderr
    events = [
        json.loads(line)
        for line in (repo / ".git" / "lumberjack-tally.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [e for e in events if e.get("event") == "session"] == [
        {**e, "event": "session", "id": "conv-42"} for e in events if e.get("event") == "session"
    ]
    assert any(e.get("event") == "session" and e.get("id") == "conv-42" for e in events)
