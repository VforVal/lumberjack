"""setup_lumberjack apply, through main() against temporary repositories."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git, write

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_lumberjack  # noqa: E402
from sweep_worktrees import load_config  # noqa: E402

BEGIN, END = "# >>> lumberjack", "# <<< lumberjack"
ANSWERS = {
    "harnesses": ["claude"],
    "owners": ["val", "claude"],
    "ticket_prefix": "eng-",
    "prototype_prefix": "proto/",
    "primary_branch": "trunk",
    "evidence_dir": "evidence",
    "worktree_roots": [".wt", "~/worktrees/{name}"],
    "cleanup": "report",
    "stop_gate": False,
    "hook": "none",
    "pin_claude_settings": False,
}


def read(root: Path, path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "plain"
    git(tmp_path, "init", "-q", "-b", "main", "plain")
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    return root


def apply(
    repo: Path, capsys: pytest.CaptureFixture[str], **answers: object
) -> tuple[int, str, str]:
    write(repo.parent, "answers.json", json.dumps({**ANSWERS, **answers}))
    try:
        code = setup_lumberjack.main(
            ["apply", str(repo.parent / "answers.json"), "--repo", str(repo)]
        )
    except SystemExit as error:
        code = error.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_config_round_trips_through_load_config(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = apply(repo, capsys)
    assert code == 0 and "wrote .lumberjack.toml" in out
    text = read(repo, ".lumberjack.toml")
    assert text.startswith("# written by lumberjack setup 1.1.1\n")
    expected = {k: v for k, v in ANSWERS.items() if k in load_config(repo)}
    assert load_config(repo) == expected == tomllib.loads(text)
    for key in expected:  # every key has a comment line directly above it
        before = text.split(f"\n{key} = ")[0].splitlines()[-1]
        assert before.startswith("# ") and key not in before.split()[0:1]


def test_unknown_key_exits_2_and_writes_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = apply(repo, capsys, bogus=1)
    assert code == 2 and "bogus" in err
    assert not (repo / ".lumberjack.toml").exists()


def test_bad_cleanup_exits_2_and_writes_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = apply(repo, capsys, cleanup="burn")
    assert code == 2 and "cleanup" in err
    assert not (repo / ".lumberjack.toml").exists()


def hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-merge"


def block_count(text: str) -> int:
    return text.count(BEGIN)


def test_plain_hook_is_written_to_githooks_and_wired(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = apply(repo, capsys, hook="plain")
    text = read(repo, ".githooks/post-merge")
    assert text.startswith("#!/bin/sh\n") and block_count(text) == 1
    assert 'python "$script" --cleanup || true' in text
    assert git(repo, "config", "core.hooksPath").strip() == ".githooks"
    assert "wrote .githooks/post-merge" in out and "ran git config core.hooksPath" in out
    script = (Path(setup_lumberjack.__file__).parent / "sweep_worktrees.py").resolve().as_posix()
    assert git(repo, "config", "lumberjack.script").strip() == script
    assert f"ran git config lumberjack.script {script}" in out
    if sys.platform != "win32":
        assert (repo / ".githooks/post-merge").stat().st_mode & 0o111


def test_plain_hook_keeps_existing_hooks_path(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "config", "core.hooksPath", "custom")
    apply(repo, capsys, hook="plain")
    assert block_count(read(repo, "custom/post-merge")) == 1
    assert git(repo, "config", "core.hooksPath").strip() == "custom"
    assert not (repo / ".githooks").exists()


@pytest.mark.parametrize("hook", ["plain", "husky", "lefthook", "pre-commit", "simple-git-hooks"])
@pytest.mark.parametrize("missing", ["script", "python", None])
def test_post_merge_runs_or_quietly_skips_missing_requirements(
    repo: Path, capsys: pytest.CaptureFixture[str], hook: str, missing: str | None,
) -> None:
    if hook == "simple-git-hooks":
        old = f"{setup_lumberjack.RUN} || true"
        write(repo, "package.json", json.dumps({"simple-git-hooks": {"post-merge": old}}))
    apply(repo, capsys, hook=hook)
    apply(repo, capsys, hook=hook)
    script = repo / "source with spaces/sweep_worktrees.py"
    git(repo, "config", "lumberjack.script", script.as_posix())
    if missing == "script":
        write(repo, "bin/python", '#!/bin/sh\necho "unexpected Python invocation" >&2\n')
        (repo / "bin/python").chmod(0o755)
    else:
        write(repo, "source with spaces/sweep_worktrees.py", 'import sys\nprint("ran", *sys.argv[1:])\n')
    if hook in ("plain", "husky"):
        command = read(repo, f"{'.githooks' if hook == 'plain' else '.husky'}/post-merge")
    elif hook == "lefthook":
        command = read(repo, "lefthook-local.yml").split("run: ", 1)[1].splitlines()[0]
    elif hook == "pre-commit":
        command = read(repo, ".pre-commit-config.yaml").split("entry: sh -c '", 1)[1].splitlines()[0][:-1]
    else:
        command = json.loads(read(repo, "package.json"))["simple-git-hooks"]["post-merge"]
        assert command.count('python "') == 1
    shell = REAL_WHICH("sh") or git(repo, "var", "GIT_SHELL_PATH").strip()
    path = str(repo / "bin") + os.pathsep + os.environ["PATH"]
    if missing == "python":
        # Git remains available, but no Python command is on this hook's PATH.
        executable = Path(REAL_WHICH("git")).as_posix()
        write(repo, "bin/git", f'#!/bin/sh\nexec "{executable}" "$@"\n')
        (repo / "bin/git").chmod(0o755)
        path = str(repo / "bin")
    done = subprocess.run(
        [shell, "-c", command], cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PATH": path},
    )
    assert (done.returncode, done.stdout, done.stderr) == (
        0, "" if missing else "ran --cleanup\n", "",
    )


def test_existing_sh_hook_gains_block_once(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".git/hooks/post-merge", "#!/bin/sh\necho mine\n")
    apply(repo, capsys, hook="plain")
    apply(repo, capsys, hook="plain")
    text = read(repo, ".git/hooks/post-merge")
    assert text.startswith("#!/bin/sh\necho mine\n") and block_count(text) == 1
    assert git(repo, "config", "--default", "unset", "core.hooksPath").strip() == "unset"


def test_non_sh_hook_is_chained_through_post_merge_user(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".git/hooks/post-merge", "#!/usr/bin/env python3\nprint('mine')\n")
    apply(repo, capsys, hook="plain")
    assert read(repo, ".git/hooks/post-merge.user").startswith("#!/usr/bin/env python3")
    text = read(repo, ".git/hooks/post-merge")
    assert text.startswith('#!/bin/sh\n"$(dirname "$0")/post-merge.user" "$@"\n')
    assert block_count(text) == 1
    apply(repo, capsys, hook="plain")  # the wrapper is sh now: no second rename
    assert block_count(read(repo, ".git/hooks/post-merge")) == 1


@pytest.mark.parametrize("prefix", ["# unrelated hook comment\n", "\n# unrelated hook comment\n\n"])
def test_rerun_preserves_comment_before_owned_hooks_shebang(
    repo: Path, capsys: pytest.CaptureFixture[str], prefix: str,
) -> None:
    apply(repo, capsys, hook="plain")
    original = read(repo, ".githooks/post-merge")
    annotated = prefix + original
    write(repo, ".githooks/post-merge", annotated)

    code, out, _ = apply(repo, capsys, hook="plain")

    assert code == 0
    assert read(repo, ".githooks/post-merge") == annotated
    assert block_count(read(repo, ".githooks/post-merge")) == 1
    assert not (repo / ".githooks/post-merge.user").exists()
    assert "ran rename" not in out


def test_python_hook_with_lumberjack_comment_is_still_wrapped(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "#!/usr/bin/env python\n# >>> lumberjack\nprint('python hook ran')\n"
    write(repo, ".git/hooks/post-merge", original)

    code, _, _ = apply(repo, capsys, hook="plain")

    assert code == 0
    assert read(repo, ".git/hooks/post-merge.user") == original
    wrapper = read(repo, ".git/hooks/post-merge")
    assert wrapper.startswith('#!/bin/sh\n"$(dirname "$0")/post-merge.user" "$@"\n')
    assert block_count(wrapper) == 1
    for name in ("post-merge", "post-merge.user"):
        path = repo / ".git/hooks" / name
        path.chmod(path.stat().st_mode | 0o111)
    git(repo, "config", "--unset", "lumberjack.script")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    done = subprocess.run(
        ["git", "hook", "run", "post-merge"], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    assert (done.stdout + done.stderr).strip() == "python hook ran"


def test_husky_hook(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, ".husky/post-merge", "echo mine\n")
    apply(repo, capsys, hook="husky")
    text = read(repo, ".husky/post-merge")
    assert text.startswith("echo mine\n") and block_count(text) == 1
    apply(repo, capsys, hook="husky")
    assert block_count(read(repo, ".husky/post-merge")) == 1


def test_lefthook_entry_goes_to_local_file(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "lefthook.yml", "pre-commit:\n  commands: {}\n")
    apply(repo, capsys, hook="lefthook")
    apply(repo, capsys, hook="lefthook")
    text = read(repo, "lefthook-local.yml")
    assert block_count(text) == 1 and "post-merge:" in text and "name: lumberjack" in text
    assert "--cleanup" in text and not (repo / ".githooks").exists()


def test_lefthook_with_post_merge_section_still_uses_the_local_file(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "lefthook.yml", "post-merge:\n  commands:\n    x:\n      run: true\n")
    apply(repo, capsys, hook="lefthook")
    assert "|| true" in read(repo, "lefthook-local.yml")
    assert not (repo / ".githooks").exists()


def test_pre_commit_entry_is_inserted_into_repos(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = "repos:\n-   repo: https://example.com/x\n    rev: v1\n    hooks:\n    -   id: x\nci:\n  a: 1\n"
    write(repo, ".pre-commit-config.yaml", config)
    apply(repo, capsys, hook="pre-commit")
    apply(repo, capsys, hook="pre-commit")
    text = read(repo, ".pre-commit-config.yaml")
    assert block_count(text) == 1 and text.endswith("ci:\n  a: 1\n")
    assert "stages: [post-merge]" in text and "always_run: true" in text
    assert "\n- repo: local\n" in text  # item indent follows the file's own list
    assert text.replace(text[text.index(BEGIN) : text.index(END) + len(END) + 1], "") == config


def test_pre_commit_creates_repos_key(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apply(repo, capsys, hook="pre-commit")
    assert read(repo, ".pre-commit-config.yaml").startswith("repos:\n")


def test_simple_git_hooks_entry(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "package.json", json.dumps({"name": "x", "simple-git-hooks": {"pre-commit": "t"}}))
    apply(repo, capsys, hook="simple-git-hooks")
    apply(repo, capsys, hook="simple-git-hooks")
    data = json.loads(read(repo, "package.json"))
    assert data["name"] == "x" and data["simple-git-hooks"]["pre-commit"] == "t"
    assert data["simple-git-hooks"]["post-merge"].count('python "') == 1
    assert read(repo, "package.json").startswith('{\n  "name"')


@pytest.mark.parametrize("guarded", [False, True])
def test_simple_git_hooks_upgrades_uv_command_once(
    repo: Path, capsys: pytest.CaptureFixture[str], guarded: bool,
) -> None:
    legacy = f"{setup_lumberjack.LEGACY_RUN} || true"
    if guarded:
        legacy = f'test -f "$(git config --get lumberjack.script)" && {legacy}'
    write(repo, "package.json", json.dumps({
        "simple-git-hooks": {"post-merge": f"echo user-hook; {legacy}"},
    }))
    apply(repo, capsys, hook="simple-git-hooks")
    apply(repo, capsys, hook="simple-git-hooks")
    command = json.loads(read(repo, "package.json"))["simple-git-hooks"]["post-merge"]
    assert command == f"echo user-hook; {setup_lumberjack.SAFE_RUN}"


def test_hook_none_writes_no_hook(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    apply(repo, capsys, hook="none")
    assert not (repo / ".githooks").exists() and not hook_path(repo).exists()


def test_settings_merge_keeps_unrelated_keys_and_is_idempotent(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = {"permissions": {"allow": ["x"]}, "extraKnownMarketplaces": {"other": {"a": 1}}}
    write(repo, ".claude/settings.json", json.dumps(mine))
    apply(repo, capsys, pin_claude_settings=True)
    first = read(repo, ".claude/settings.json")
    apply(repo, capsys, pin_claude_settings=True)
    assert read(repo, ".claude/settings.json") == first
    data = json.loads(first)
    assert data["permissions"] == {"allow": ["x"]}
    assert data["extraKnownMarketplaces"]["other"] == {"a": 1}
    assert data["extraKnownMarketplaces"]["lumberjack"] == {
        "source": {"source": "github", "repo": "VforVal/lumberjack"},
        "autoUpdate": False,
    }
    assert data["enabledPlugins"] == {"lumberjack@lumberjack": True}


def test_settings_untouched_without_pin_or_claude(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apply(repo, capsys, pin_claude_settings=False)
    apply(repo, capsys, pin_claude_settings=True, harnesses=["codex"])
    assert not (repo / ".claude").exists()


BLOCK_MD = "## Lumberjack\n\nWorktree rules live in the `lumberjack` skill; config in `.lumberjack.toml`.\n"


def test_instructions_go_to_claude_md_else_agents_md_created_when_absent(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    apply(repo, capsys)
    assert not (repo / "CLAUDE.md").exists()
    assert BLOCK_MD in read(repo, "AGENTS.md")
    write(repo, "AGENTS.md", "# agents\n")
    apply(repo, capsys)
    assert BLOCK_MD in read(repo, "AGENTS.md")
    write(repo, "CLAUDE.md", "# claude\n")
    apply(repo, capsys)
    assert BLOCK_MD in read(repo, "CLAUDE.md")
    assert read(repo, "AGENTS.md").count("<!-- >>> lumberjack -->") == 1


def test_instructions_rerun_replaces_only_the_block(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "CLAUDE.md", "# top\n")
    apply(repo, capsys)
    text = read(repo, "CLAUDE.md").replace("Re-run", "STALE") + "# bottom\n"
    write(repo, "CLAUDE.md", text.replace("# top", "# top edited"))
    apply(repo, capsys)
    now = read(repo, "CLAUDE.md")
    assert now.startswith("# top edited\n") and now.endswith("<!-- <<< lumberjack -->\n# bottom\n")
    assert "STALE" not in now and now.count("## Lumberjack") == 1


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md"])
def test_instructions_rerun_preserves_unowned_crlf_bytes(
    repo: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    if name == "CLAUDE.md":
        write(repo, name, "# notes\n")
    assert apply(repo, capsys)[0] == 0
    path = repo / name
    before = path.read_bytes() + b"\r\nUnrelated preservation sentinel.\r\n"
    path.write_bytes(before)

    assert apply(repo, capsys)[0] == 0
    assert path.read_bytes() == before


def test_gitignore_gets_in_repo_roots_once(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".gitignore", "node_modules")
    roots = [".wt", "./.claude/worktrees/", "~/wt", "/abs/wt", "../up", "x/{name}"]
    apply(repo, capsys, worktree_roots=roots)
    apply(repo, capsys, worktree_roots=roots)
    assert read(repo, ".gitignore") == "node_modules\n.wt/\n.claude/worktrees/\n"


def test_codex_lines_are_printed_not_run(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = apply(repo, capsys, harnesses=["codex"])
    assert "Re-run the `lumberjack:setup` skill" in read(repo, "AGENTS.md")
    for line in (
        "codex plugin marketplace add VforVal/lumberjack",
        "codex plugin add lumberjack@lumberjack",
        "then run /hooks once in Codex to trust the plugin hooks",
    ):
        assert f"printed, not run: {line}" in out
    assert "codex plugin" not in apply(repo, capsys, harnesses=["claude"])[1]


def test_claude_lines_are_printed_not_run(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = apply(repo, capsys, harnesses=["claude"])
    for line in (
        "claude plugin marketplace add VforVal/lumberjack",
        "claude plugin install lumberjack@lumberjack",
    ):
        assert f"printed, not run: {line}" in out
    assert "claude plugin" not in apply(repo, capsys, harnesses=["codex"])[1]


def test_uv_missing_still_installs_hook_config_and_settings(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    _, out, _ = apply(repo, capsys, hook="plain", pin_claude_settings=True)
    assert "uv" not in out
    assert (repo / ".lumberjack.toml").is_file() and (repo / ".claude/settings.json").is_file()
    assert (repo / ".githooks/post-merge").is_file()


def test_gh_missing_or_unauthenticated_line(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    line = "gh missing or unauthenticated: landed detection is offline-only until gh auth login"
    assert line in apply(repo, capsys)[1]
    monkeypatch.setattr(setup_lumberjack, "gh_facts", lambda: {"present": True, "authenticated": True})
    assert line not in apply(repo, capsys)[1]
    monkeypatch.setattr(setup_lumberjack, "gh_facts", lambda: {"present": True, "authenticated": False})
    assert line in apply(repo, capsys)[1]


def test_second_apply_rewrites_only_owned_text(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, "CLAUDE.md", "# top\n")
    write(repo, ".git/hooks/post-merge", "#!/bin/sh\necho mine\n")
    write(repo, ".claude/settings.json", json.dumps({"keep": 1}))
    apply(repo, capsys, hook="plain", pin_claude_settings=True)
    write(repo, "CLAUDE.md", read(repo, "CLAUDE.md") + "# bottom\n")
    write(repo, ".git/hooks/post-merge", read(repo, ".git/hooks/post-merge") + "echo after\n")
    apply(repo, capsys, hook="plain", pin_claude_settings=True, cleanup="keep", owners=["zed"])
    cfg = load_config(repo)
    assert cfg["cleanup"] == "keep" and cfg["owners"] == ["zed"]
    assert read(repo, "CLAUDE.md").endswith("# bottom\n")
    hook = read(repo, ".git/hooks/post-merge")
    assert hook.startswith("#!/bin/sh\necho mine\n") and hook.endswith("echo after\n")
    assert block_count(hook) == 1
    assert json.loads(read(repo, ".claude/settings.json"))["keep"] == 1


def test_invalid_settings_json_exits_2_before_writing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, ".claude/settings.json", "{nope")
    code, _, err = apply(repo, capsys, pin_claude_settings=True)
    assert code == 2 and "settings.json" in err and not (repo / ".lumberjack.toml").exists()


SWEEP = Path(__file__).resolve().parents[1] / "scripts" / "sweep_worktrees.py"


def merge(cwd: Path, branch: str) -> str:
    """Merge and return everything the hook printed (git sends hook stdout to stderr)."""
    done = subprocess.run(
        ["git", "merge", "--no-ff", "-m", f"merge {branch}", branch],
        cwd=cwd, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout + done.stderr


def test_plain_hook_runs_the_sweep_on_a_real_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", REAL_WHICH)
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", "primary")
    primary = tmp_path / "primary"
    git(primary, "commit", "-q", "--allow-empty", "-m", "root")
    git(primary, "push", "-q", "origin", "main")
    git(primary, "config", "lumberjack.script", SWEEP.as_posix())
    apply(primary, capsys, hook="plain", primary_branch="main", worktree_roots=[])
    git(primary, "add", "-A")
    git(primary, "update-index", "--chmod=+x", ".githooks/post-merge")
    git(primary, "commit", "-q", "-m", "lumberjack setup")
    git(primary, "push", "-q", "origin", "main")
    for name in ("val/one", "val/two"):
        git(primary, "switch", "-q", "-c", name, "main")
        git(primary, "commit", "-q", "--allow-empty", "-m", name)
        git(primary, "push", "-q", "origin", name)
    git(primary, "switch", "-q", "main")

    assert "sweep: cleaned" in merge(primary, "val/one")

    git(primary, "worktree", "add", "-q", "-b", "val/three", str(tmp_path / "linked"), "main")
    assert "sweep: not the primary checkout; skipped" in merge(tmp_path / "linked", "val/two")


def test_plain_hook_follows_hooks_path_outside_repo(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    hooks = tmp_path / "hooks"
    write(hooks, "post-merge", "#!/bin/sh\necho mine\n")
    git(repo, "config", "core.hooksPath", hooks.as_posix())
    code, _, _ = apply(repo, capsys, hook="plain")
    assert code == 0
    text = (hooks / "post-merge").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\necho mine\n") and block_count(text) == 1
    assert not (repo / "~").exists() and not (repo / "hooks").exists()


def link_dir(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:  # Windows without symlink rights: a junction needs none
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True)


def test_link_out_of_the_repo_is_refused(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    write(outside, "settings.json", "{}\n")
    link_dir(repo / ".claude", outside)
    code, _, err = apply(repo, capsys, pin_claude_settings=True)
    assert code == 2 and "outside the repository" in err
    assert (outside / "settings.json").read_text(encoding="utf-8") == "{}\n"


def test_linked_hooks_dir_keeps_its_foreign_hook_in_place(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    write(outside, "post-merge", "#!/usr/bin/env python3\nprint('mine')\n")
    link_dir(repo / ".githooks", outside)
    git(repo, "config", "core.hooksPath", ".githooks")
    code, _, err = apply(repo, capsys, hook="plain")
    assert code == 2 and "outside the repository" in err
    assert (outside / "post-merge").read_text(encoding="utf-8").endswith("print('mine')\n")
    assert not (outside / "post-merge.user").exists()
