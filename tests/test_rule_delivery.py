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


def run_hook_command(
    command: str,
    cwd: Path,
    payload: dict[str, object],
    *shims: str,
    use_sh: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a hook command through the host shell with uv hidden from PATH."""
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
        shell = REAL_WHICH("sh") or git(cwd, "var", "GIT_SHELL_PATH").strip()
        argv = [shell, "-c", command]
    return subprocess.run(
        argv,
        cwd=cwd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path},
    )


def make_python_shims(tmp_path: Path, stale: str) -> Path:
    # Stock macOS and Ubuntu 22.04 ship an old python3; CentOS 7 has python 2. The launcher
    # must pick the other name by version, not by position.
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    (shims / stale).write_text("#!/bin/sh\necho Python 3.9.6\n", encoding="utf-8", newline="\n")
    (shims / ("python" if stale == "python3" else "python3")).write_text(
        f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    for shim in shims.iterdir():
        shim.chmod(0o755)
    return shims


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
    return run_hook_command(command, repo, {"hook_event_name": event}, *shims, use_sh=True)


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
    shims = make_python_shims(tmp_path, stale)
    done = registered_hook(tmp_path, "Stop", str(shims))
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert json.loads(done.stdout)["decision"] == "block"


def stage_antigravity_plugin(plugin: Path) -> dict[str, list[dict[str, object]]]:
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "scripts", plugin / "scripts", dirs_exist_ok=True)
    shutil.copytree(root / "skills", plugin / "skills", dirs_exist_ok=True)
    for name in ("plugin.json", "hooks.json", "VERSION"):
        shutil.copyfile(root / name, plugin / name)
    return json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))["lumberjack"]


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
    hooks = stage_antigravity_plugin(plugin)

    def invoke(event: str, payload: dict[str, object]) -> dict[str, object]:
        done = run_hook_command(str(hooks[event][0]["command"]), plugin, payload)
        assert done.returncode == 0, done.stderr
        assert done.stderr == ""
        return json.loads(done.stdout)

    ctx = invoke(
        "SessionStart",
        {"hookEventName": "SessionStart", "conversationId": "conv-1", "cwd": str(repo), "workspacePaths": [str(repo)]},
    )
    msg = ctx["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "Worktree" in msg and "Active lumberjack settings" in msg

    plain_dir = tmp_path / "plain folder"
    plain_dir.mkdir(exist_ok=True)
    # Non-git cwd (or plain folder ahead in workspacePaths) falls through to the git repo workspace.
    ctx_ws = invoke(
        "SessionStart",
        {"hookEventName": "SessionStart", "cwd": str(plain_dir), "workspacePaths": [str(plain_dir), str(repo)]},
    )
    assert "Worktree" in ctx_ws["injectSteps"][0]["systemMessage"]["systemMessage"]

    unconfigured = tmp_path / "unconfigured repo"
    git(tmp_path, "init", "-q", "-b", "main", str(unconfigured))
    git(unconfigured, "commit", "-q", "--allow-empty", "-m", "root")
    ctx_unconf = invoke(
        "SessionStart",
        {"hookEventName": "SessionStart", "workspacePaths": [str(unconfigured)]},
    )
    assert ctx_unconf["injectSteps"][0]["systemMessage"]["systemMessage"] == sweep_worktrees.NOT_SET_UP

    for empty_payload in (
        {"hookEventName": "SessionStart", "cwd": str(plain_dir), "workspacePaths": [str(plain_dir)]},
        {"hookEventName": "SessionStart", "cwd": "", "workspacePaths": []},
    ):
        assert invoke("SessionStart", empty_payload) == {}
        assert invoke("Stop", {**empty_payload, "hookEventName": "Stop"}) == {"decision": "allow"}

    stop_out = invoke(
        "Stop",
        {
            "hookEventName": "Stop",
            "terminationReason": "NO_TOOL_CALL",
            "continuationActive": False,
            "cwd": str(repo),
            "workspacePaths": [str(repo)],
        },
    )
    assert stop_out["decision"] == "continue"
    assert "branch Feature_X violates" in stop_out["reason"]

    for skip_payload in (
        {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "continuationActive": True, "cwd": str(repo)},
        {"hookEventName": "Stop", "terminationReason": "USER_CANCELED", "continuationActive": False, "cwd": str(repo)},
    ):
        assert invoke("Stop", skip_payload) == {"decision": "allow"}

    write(repo, ".lumberjack.toml", 'cleanup = "report"\nstop_gate = false\nworktree_roots = []\n')
    assert invoke("Stop", {"hookEventName": "Stop", "cwd": str(repo)}) == {"decision": "allow"}

    write(repo, ".lumberjack.toml", "[unclosed\n")
    err_out = invoke("Stop", {"hookEventName": "Stop", "cwd": str(repo)})
    assert err_out["decision"] == "continue" and "bad TOML" in err_out["reason"]

    write(repo, ".lumberjack.toml", 'cleanup = "report"\nworktree_roots = []\n')
    git(repo, "checkout", "-q", "-b", "main")
    assert invoke(
        "Stop",
        {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(repo)},
    ) == {"decision": "allow"}


@pytest.mark.parametrize("stale", ["python3", "python"])
def test_antigravity_hook_skips_old_python(tmp_path: Path, stale: str) -> None:
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", "-b", "Feature_X", str(repo))
    git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    write(repo, ".lumberjack.toml", 'cleanup = "report"\nworktree_roots = []\n')

    shims = make_python_shims(tmp_path, stale)
    plugin = tmp_path / "plugin"
    hooks = stage_antigravity_plugin(plugin)
    done = run_hook_command(
        str(hooks["Stop"][0]["command"]),
        plugin,
        {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(repo)},
        str(shims),
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
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

    plugin = tmp_path / "plugin"
    hooks = stage_antigravity_plugin(plugin)
    done = run_hook_command(
        str(hooks["SessionStart"][0]["command"]),
        plugin,
        {"hookEventName": "SessionStart", "conversationId": "conv-42", "cwd": str(repo)},
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    events = [
        json.loads(line)
        for line in (repo / ".git" / "lumberjack-tally.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [e.get("id") for e in events if e.get("event") == "session"] == ["conv-42"]
