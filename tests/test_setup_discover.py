"""setup_lumberjack discover, through main() against temporary repositories."""

import contextlib
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git, write

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_lumberjack  # noqa: E402

KEYS = {
    "repo", "name", "harness", "origin", "remote_shapes", "ticket_prefix_guess",
    "owners_guess", "worktree_parents", "tracker", "hook_manager", "post_merge_hook",
    "hooks_path", "git_version", "python", "gh", "config", "doc_root_candidates",
}  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "plain"
    git(tmp_path, "init", "-q", "-b", "main", "plain")
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    return root


@pytest.fixture
def cloned(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", "primary")
    root = tmp_path / "primary"
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    git(root, "push", "-q", "origin", "main")
    return root


def discover(capsys: pytest.CaptureFixture[str], repo: Path) -> dict[str, object]:
    assert setup_lumberjack.main(["discover", "--repo", str(repo)]) == 0
    return json.loads(capsys.readouterr().out)


def test_plain_repo_without_origin_has_every_key(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    facts = discover(capsys, repo)
    assert set(facts) == KEYS
    assert facts["name"] == "plain"
    assert facts["origin"] == {"url": None, "owner": None, "name": None, "default_branch": None}
    assert facts["remote_shapes"] == [] and facts["ticket_prefix_guess"] == ""
    assert facts["hook_manager"] is None and facts["post_merge_hook"] is None
    assert facts["python"] == sys.version.split()[0]
    assert facts["gh"] == {"present": False, "authenticated": None}
    assert facts["config"] == {"source": None, "error": None}


def test_git_missing_exits_with_install_instructions(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit) as error:
        setup_lumberjack.main(["discover", "--repo", str(repo)])
    assert error.value.code == 2
    assert "git missing: install git from https://git-scm.com/downloads and re-run" in capsys.readouterr().err
    assert not (repo / ".lumberjack.toml").exists()


def test_harness_flags(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "AGENTS.md", "x")
    write(repo, ".claude/settings.json", "{}")
    assert discover(capsys, repo)["harness"] == {
        "claude_md": False, "agents_md": True, "claude_settings": True, "codex_dir": False,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("url", "owner", "name"),
    [
        ("https://github.com/acme/widgets.git", "acme", "widgets"),
        ("git@github.com:acme/widgets.git", "acme", "widgets"),
        ("https://github.com/acme/widgets", "acme", "widgets"),
    ],
)
def test_origin_urls(
    repo: Path, capsys: pytest.CaptureFixture[str], url: str, owner: str, name: str
) -> None:
    git(repo, "remote", "add", "origin", url)
    origin = discover(capsys, repo)["origin"]
    assert (origin["url"], origin["owner"], origin["name"]) == (url, owner, name)


def test_default_branch_from_remote_refs(
    cloned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert discover(capsys, cloned)["origin"]["default_branch"] == "main"


def test_remote_branch_guesses(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for branch in ("claude/eng-1-x", "claude/eng-3-z", "codex/eng-2-y"):
        git(cloned, "push", "-q", "origin", f"main:{branch}")
    git(cloned, "fetch", "-q", "origin")
    facts = discover(capsys, cloned)
    assert facts["ticket_prefix_guess"] == "eng-"
    assert facts["owners_guess"] == ["claude", "codex"]
    assert facts["remote_shapes"] == [
        {"shape": "claude/*", "count": 2},
        {"shape": "*", "count": 1},
        {"shape": "codex/*", "count": 1},
    ]


def test_ticket_token_in_shape(cloned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    git(cloned, "push", "-q", "origin", "main:team/eng-9/slug")
    git(cloned, "fetch", "-q", "origin")
    shapes = [row["shape"] for row in discover(capsys, cloned)["remote_shapes"]]
    assert "team/<ticket>/*" in shapes


def test_worktree_parents(
    cloned: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(cloned, "worktree", "add", "-q", "-b", "a", str(tmp_path / "wts" / "a"))
    git(cloned, "worktree", "add", "-q", "-b", "b", str(tmp_path / "home" / "wt" / "b"))
    assert discover(capsys, cloned)["worktree_parents"] == [
        (tmp_path / "wts").as_posix(),
        "~/wt",
    ]


def test_tracker_doc(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert discover(capsys, repo)["tracker"]["doc"] is None
    write(repo, "docs/agents/issue-tracker.md", "Tickets look like ENG-12.\n")
    tracker = discover(capsys, repo)["tracker"]
    assert (tracker["ticket_key"], tracker["prefix"]) == ("ENG-12", "eng-")
    assert tracker["doc"].endswith("issue-tracker.md")


@pytest.mark.parametrize(
    ("path", "text", "manager"),
    [
        (".husky/pre-commit", "", "husky"),
        ("lefthook.yml", "", "lefthook"),
        (".lefthook.yml", "", "lefthook"),
        (".pre-commit-config.yaml", "", "pre-commit"),
        ("package.json", '{"simple-git-hooks": {}}', "simple-git-hooks"),
        ("package.json", "{}", None),
    ],
)
def test_hook_manager(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str, text: str, manager: str | None
) -> None:
    write(repo, path, text)
    assert discover(capsys, repo)["hook_manager"] == manager


def test_post_merge_hook_and_hooks_path(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".git/hooks/post-merge", "#!/bin/sh\necho hi\n")
    hook = discover(capsys, repo)["post_merge_hook"]
    assert (hook["is_sh"], hook["has_marker"]) == (True, False)
    write(repo, ".githooks/post-merge", "#!/usr/bin/env python3\n# >>> lumberjack\n")
    git(repo, "config", "core.hooksPath", ".githooks")
    facts = discover(capsys, repo)
    hook = facts["post_merge_hook"]
    assert facts["hooks_path"] == ".githooks"
    assert ".githooks" in hook["path"]
    assert (hook["is_sh"], hook["has_marker"]) == (False, True)


def test_tilde_hooks_path_is_expanded(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    write(tmp_path / "home", ".githooks/post-merge", "#!/bin/sh\necho hi\n")
    git(repo, "config", "core.hooksPath", "~/.githooks")
    facts = discover(capsys, repo)
    home = (tmp_path / "home").resolve().as_posix()
    assert Path(facts["hooks_path"]).resolve().as_posix() == f"{home}/.githooks"
    assert Path(facts["post_merge_hook"]["path"]).resolve().as_posix().startswith(home)


def test_config_echoed_and_errors(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "pyproject.toml", '[tool.lumberjack]\nticket_prefix = "eng-"\n')
    config = discover(capsys, repo)["config"]
    assert (config["source"], config["ticket_prefix"], config["error"]) == (
        "pyproject.toml", "eng-", None,
    )  # fmt: skip
    write(repo, ".lumberjack.toml", 'owners = ["claude"]\n')
    config = discover(capsys, repo)["config"]
    assert (config["source"], config["owners"]) == (".lumberjack.toml", ["claude"])
    write(repo, ".lumberjack.toml", "owners = [\n")
    config = discover(capsys, repo)["config"]
    assert config["source"] == ".lumberjack.toml" and "bad TOML" in config["error"]


def test_doc_root_candidates(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert discover(capsys, repo)["doc_root_candidates"] == []
    write(repo, ".gitignore", "work/\nbuild/\nnode_modules/\n.claude/\nsub/inner/\n")
    write(repo, ".scratch/handoff.txt", "x")
    write(repo, "work/deep/plan.MD", "x")
    write(repo, "build/out.bin", "x")
    write(repo, "node_modules/pkg/README.md", "x")
    write(repo, ".claude/worktrees/a/README.md", "x")
    write(repo, "untracked/notes.md", "x")
    assert discover(capsys, repo)["doc_root_candidates"] == [
        {"path": ".scratch", "reason": "conventional scratch folder for agent work documents"},
        {"path": "work", "reason": "ignored folder holding Markdown"},
    ]  # fmt: skip
    write(repo, ".lumberjack.toml", 'doc_roots = ["docs/work"]\n')  # a re-run sees the saved value
    assert discover(capsys, repo)["config"]["doc_roots"] == ["docs/work"]


def test_doc_root_scan_is_bounded(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo, ".gitignore", "work/\n")
    for number in range(300):
        write(repo, f"work/f{number}.txt", "x")
    examined = []
    real = os.scandir

    @contextlib.contextmanager
    def counting(path):  # type: ignore[no-untyped-def]
        with real(path) as entries:
            yield (examined.append(entry) or entry for entry in entries)

    monkeypatch.setattr(setup_lumberjack.os, "scandir", counting)
    assert discover(capsys, repo)["doc_root_candidates"] == []
    assert len(examined) <= setup_lumberjack.DOC_SCAN + 1


def shim_gh(root: Path, monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    shim = root / "shim"
    shim.mkdir()
    (shim / "gh").write_text(f"#!/bin/sh\nexit {code}\n", newline="\n")
    (shim / "gh").chmod(0o755)
    (shim / "gh.cmd").write_text(f"@exit /b {code}\r\n", newline="")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("shutil.which", REAL_WHICH)


@pytest.mark.parametrize(("code", "authenticated"), [(0, True), (1, False)])
def test_gh_presence(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: int,
    authenticated: bool,
) -> None:
    shim_gh(tmp_path, monkeypatch, code)
    facts = discover(capsys, repo)
    assert facts["gh"] == {"present": True, "authenticated": authenticated}

