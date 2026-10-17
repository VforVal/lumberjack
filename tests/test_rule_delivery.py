"""The shared plugin lifecycle registration delivers rules and sweeps once per event."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git, write


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


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart", "Stop"])
def test_registered_hook_runs_with_plain_python(
    tmp_path: Path, event: str,
) -> None:
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
    # Use the registered command through the host shell, with no uv on PATH.
    uv = REAL_WHICH("uv")
    path = os.pathsep.join(
        part for part in os.environ["PATH"].split(os.pathsep)
        if not uv or Path(part).resolve() != Path(uv).parent.resolve()
    )
    done = subprocess.run(
        command, shell=True, cwd=repo, input=json.dumps({"hook_event_name": event}),
        capture_output=True, text=True, env={**os.environ, "PATH": path},
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    output = json.loads(done.stdout)
    if event == "Stop":
        assert output["decision"] == "block"
        assert "branch Feature_X violates" in output["reason"]
    else:
        assert "Worktree" in output["hookSpecificOutput"]["additionalContext"]
