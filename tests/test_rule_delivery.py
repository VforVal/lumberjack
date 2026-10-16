"""The shared plugin lifecycle registration delivers rules and sweeps once per event."""

import json
import re
from pathlib import Path

import pytest


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
