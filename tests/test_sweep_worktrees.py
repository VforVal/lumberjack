"""Sweep classification and pruning against a throwaway origin and clone."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sweep_worktrees  # noqa: E402


def add_worktree(primary: Path, root: Path, branch: str, merge: bool) -> Path:
    path = root / branch.split("/")[1]
    git(primary, "worktree", "add", "-q", "-b", branch, str(path))
    (path / f"{path.name}.txt").write_text("x\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", branch)
    if merge:
        git(primary, "merge", "-q", "--ff-only", branch)
    return path


@pytest.fixture
def primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, "clone", "-q", "origin.git", "primary")
    root = tmp_path / "primary"
    (root / "pyproject.toml").write_text(
        "[tool.lumberjack]\n"
        'owners = ["claude"]\nticket_prefix = "eng-"\nevidence_dir = ".evidence"\n'
    )
    git(root, "add", "pyproject.toml")
    git(root, "commit", "-q", "-m", "root")
    git(root, "push", "-q", "origin", "main")
    wt = tmp_path / "wt"
    add_worktree(root, wt, "claude/eng-1-clean", merge=True)
    dirty = add_worktree(root, wt, "claude/eng-2-dirty", merge=True)
    (dirty / "scratch.txt").write_text("uncommitted\n")
    add_worktree(root, wt, "claude/eng-3-open", merge=False)
    git(root, "branch", "claude/eng-4-loose", "claude/eng-3-open")
    git(root, "branch", "feature/x", "claude/eng-3-open")
    git(root, "branch", "prototype/eng-65-demo", "main")
    git(root, "push", "-q", "origin", "main")
    git(root, "fetch", "-q", "origin")
    monkeypatch.chdir(root)
    monkeypatch.setattr(sweep_worktrees, "orphan_candidates", lambda *_: [])
    return root


def report(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, object]:
    assert sweep_worktrees.main(["--json", *args]) == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def test_classifies_worktrees_branches_and_primary(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = report(capsys)
    states = {
        w["branch"]: (w["state"], w["dirty"])
        for w in data["worktrees"]  # type: ignore[attr-defined]
    }
    assert states == {
        "claude/eng-1-clean": ("integrated", 0),
        "claude/eng-2-dirty": ("integrated", 1),
        "claude/eng-3-open": ("open", 0),
    }
    assert {b["name"] for b in data["branches"]} == {  # type: ignore[attr-defined]
        "claude/eng-4-loose",
        "feature/x",
    }
    assert data["name_violations"] == ["feature/x"]
    assert data["prototypes"] == [{"issue": "eng-65", "name": "prototype/eng-65-demo"}]
    assert data["primary"] == {"branch": "main", "dirty": 0, "warning": False}


def test_gate_objects_once_to_bad_branch_or_primary_off_main(
    primary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert sweep_worktrees.main(["--gate"]) == 0
    git(primary, "checkout", "-q", "-b", "Feature_X")
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert sweep_worktrees.main(["--gate"]) == 2
    err = capsys.readouterr().err
    assert "branch Feature_X violates" in err
    assert "primary checkout is on Feature_X" in err
    monkeypatch.setattr("sys.stdin", io.StringIO('{"stop_hook_active": true}'))
    assert sweep_worktrees.main(["--gate"]) == 0


def test_orphan_classification(
    primary: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    only = tmp_path / "orphans" / "only"
    (only / "node_modules").mkdir(parents=True)
    (only / ".venv").mkdir()
    mixed = tmp_path / "orphans" / "mixed"
    (mixed / "src").mkdir(parents=True)
    (mixed / ".git").write_text("gitdir: gone\n")
    plain = tmp_path / "orphans" / "plain"
    (plain / "src").mkdir(parents=True)
    empty = tmp_path / "orphans" / "empty"
    empty.mkdir()
    registered = tmp_path / "wt" / "eng-3-open"
    monkeypatch.setattr(
        sweep_worktrees,
        "orphan_candidates",
        lambda *_: [only, mixed, plain, empty, registered],
    )
    orphans = report(capsys)["orphans"]
    assert {o["path"]: o["kind"] for o in orphans} == {  # type: ignore[attr-defined]
        only.as_posix(): "node_modules-only",
        mixed.as_posix(): "mixed",
        plain.as_posix(): "not-git",
        empty.as_posix(): "not-git",
    }
    assert sweep_worktrees.main(["--cleanup"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert {"cleaned orphan orphans/only", "orphan mixed orphans/mixed"} <= set(out)
    assert "orphan not-git orphans/plain" in out
    assert out[0].endswith("orphans 4")
    assert not only.exists() and mixed.exists() and plain.exists() and empty.exists()


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("claude/eng-115-x", True),
        ("claude/eng-116-add-scriptssweep_worktreespy-and-x", True),
        ("claude/spike", True),
        ("codex/eng-1-x", False),
        ("prototype/eng-65-reviewer-dashboard", True),
        ("feature/x", False),
        ("prototype/dashboard", False),
        ("Claude/eng-1-x", False),
    ],
)
def test_branch_name_convention(name: str, valid: bool) -> None:
    cfg = {**sweep_worktrees.DEFAULTS, "owners": ["claude"], "ticket_prefix": "eng-"}
    pattern = sweep_worktrees.branch_pattern(cfg)
    assert bool(pattern.match(name)) is valid


GH_PY = r"""
import json, os, re, sys, time
with open(os.environ["GH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:3]) + "\n")
fixture = json.load(open(os.environ["GH_FIXTURE"]))
time.sleep(fixture.get("sleep", 0))
if fixture.get("exit"):
    sys.stderr.write(fixture.get("stderr", ""))
    sys.exit(fixture["exit"])
query = " ".join(sys.argv)
repo = {
    f"b{i}": {"nodes": fixture["prs"].get(name, [])}
    for i, name in re.findall(r'b(\d+): pullRequests\(headRefName: "([^"]*)"', query)
}
print(json.dumps({"data": {"repository": repo}}))
"""


class Gh:
    """A fake gh on PATH: answers `api graphql` from a JSON fixture and logs each call."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fixture, self.log = root / "fixture.json", root / "calls.log"
        shim = root / "shim"
        shim.mkdir()
        (shim / "gh.py").write_text(GH_PY)
        (shim / "gh").write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{shim}/gh.py" "$@"\n', newline="\n"
        )
        (shim / "gh").chmod(0o755)
        (shim / "gh.cmd").write_text(
            f'@"{sys.executable}" "{shim}\\gh.py" %*\r\n', newline=""
        )
        monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("GH_FIXTURE", str(self.fixture))
        monkeypatch.setenv("GH_LOG", str(self.log))
        monkeypatch.setattr("shutil.which", REAL_WHICH)
        self.set()

    def set(
        self, prs: dict[str, list[dict[str, str]]] | None = None, **fields: object
    ) -> None:
        self.fixture.write_text(json.dumps({"prs": prs or {}, **fields}))

    def calls(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0


def fake_github(primary: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> Gh:
    """Make origin look like GitHub for gh only; call it after the test pushed."""
    git(primary, "remote", "set-url", "origin", "https://github.com/o/r.git")
    return Gh(root, monkeypatch)


@pytest.fixture
def gh(primary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gh:
    return fake_github(primary, tmp_path, monkeypatch)


def land(primary: Path, name: str, text: str) -> None:
    """Commit a file on main and move origin/main to it, as a merge elsewhere would."""
    (primary / name).write_text(text)
    git(primary, "add", name)
    git(primary, "commit", "-q", "-m", f"land {name}")
    git(primary, "update-ref", "refs/remotes/origin/main", "HEAD")


def conflicted_branch(primary: Path, name: str) -> str:
    """A worktree branch whose file also exists, differently, on main; returns its tip."""
    path = add_worktree(primary, primary.parent / "wt", f"claude/{name}", merge=False)
    land(primary, f"{name}.txt", "main\n")
    return git(path, "rev-parse", "HEAD").strip()


def tiers(
    capsys: pytest.CaptureFixture[str],
) -> dict[str, tuple[str, str]]:
    data = report(capsys)
    rows = [*data["worktrees"], *data["branches"]]  # type: ignore[misc]
    return {(r.get("branch") or r["name"]): (r["tier"], r["reason"]) for r in rows}


def pr(oid: str, base: str = "main") -> dict[str, str]:
    return {"headRefOid": oid, "baseRefName": base}


def test_tier_integrated_and_gh_missing(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    found = tiers(capsys)
    assert found["claude/eng-1-clean"] == ("integrated", "")
    assert found["claude/eng-3-open"] == ("unverified", "gh missing")
    assert found["feature/x"] == ("unverified", "gh missing")


def test_tier_content_integrated(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_worktree(primary, primary.parent / "wt", "claude/eng-8-same", merge=False)
    land(primary, "eng-8-same.txt", "x\n")
    assert tiers(capsys)["claude/eng-8-same"] == ("content-integrated", "")


def test_tier_landed_pr_needs_the_exact_tip(
    primary: Path, gh: Gh, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = conflicted_branch(primary, "eng-9-squash")
    gh.set({"claude/eng-9-squash": [pr(tip)]})
    data = report(capsys)
    row = next(w for w in data["worktrees"] if w["branch"] == "claude/eng-9-squash")  # type: ignore[attr-defined]
    assert (row["tier"], row["state"]) == ("landed-pr", "integrated")
    gh.set({"claude/eng-9-squash": [pr("0" * 40)]})
    assert tiers(capsys)["claude/eng-9-squash"] == ("unverified", "tip diverged")


def test_non_primary_base_and_no_pull_request(
    primary: Path, gh: Gh, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = conflicted_branch(primary, "eng-9-stacked")
    gh.set({"claude/eng-9-stacked": [pr(tip, "claude/other")]})
    found = tiers(capsys)
    assert found["claude/eng-9-stacked"] == ("unverified", "non-primary base")
    assert found["claude/eng-3-open"] == ("unverified", "no pull request")


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"exit": 4, "stderr": "To authenticate, run: gh auth login"}, "unauthenticated"),
        ({"exit": 1, "stderr": "dial tcp: lookup api.github.com: no such host"}, "unreachable"),
        ({"sleep": 3}, "unreachable"),
    ],
)
def test_gh_failures_leave_the_branch_unverified(
    primary: Path,
    gh: Gh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fields: dict[str, object],
    reason: str,
) -> None:
    monkeypatch.setattr(sweep_worktrees, "GH_TIMEOUT", 1)
    gh.set(**fields)
    assert tiers(capsys)["claude/eng-3-open"] == ("unverified", reason)


def test_landed_probable_by_cherry_and_by_patch_id(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wt = primary.parent / "wt"
    add_worktree(primary, wt, "claude/eng-10-cherry", merge=False)
    land(primary, "unrelated.txt", "u\n")
    git(primary, "cherry-pick", "claude/eng-10-cherry")
    land(primary, "eng-10-cherry.txt", "x\nmore\n")  # a later change hides the merge
    squash = add_worktree(primary, wt, "claude/eng-11-squash", merge=False)
    (squash / "second.txt").write_text("2\n")
    git(squash, "add", ".")
    git(squash, "commit", "-q", "-m", "second")
    # Two branch commits land as one main commit, then main edits both files.
    for name in ("eng-11-squash.txt", "second.txt"):
        (primary / name).write_text("x\n" if name.startswith("eng") else "2\n")
    git(primary, "add", ".")
    git(primary, "commit", "-q", "-m", "squash")
    land(primary, "eng-11-squash.txt", "x\nmore\n")
    land(primary, "second.txt", "2\nmore\n")
    found = tiers(capsys)
    assert found["claude/eng-10-cherry"] == ("landed-probable", "gh missing")
    assert found["claude/eng-11-squash"] == ("landed-probable", "gh missing")


def test_one_gh_call_per_sweep(
    primary: Path, gh: Gh, capsys: pytest.CaptureFixture[str]
) -> None:
    report(capsys)
    assert gh.calls() == 1


def test_stale_origin_is_appended_to_the_reason(primary: Path) -> None:
    cfg = sweep_worktrees.load_config(primary)
    tip = git(primary, "rev-parse", "claude/eng-3-open").strip()
    found = sweep_worktrees.classify(
        primary, cfg, {(tip, "claude/eng-3-open")}, stale=True
    )
    assert found[(tip, "claude/eng-3-open")] == (
        "unverified",
        "gh missing; origin stale",
    )


SCRIPT = Path(sweep_worktrees.__file__).resolve()


def sweep(
    primary: Path, *args: str, stdin: str = "{}", cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the script as the hooks do; HOME is empty and fetches over HTTP fail fast."""
    home = str(primary.parent / "home")
    env = {
        **os.environ,
        "HOME": home,
        "USERPROFILE": home,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.proxy",
        "GIT_CONFIG_VALUE_0": "http://127.0.0.1:9",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or primary,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
    )


def cleanup(primary: Path) -> list[str]:
    done = sweep(primary, "--cleanup")
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def settings(primary: Path, extra: str = "") -> None:
    (primary / ".lumberjack.toml").write_text(
        'owners = ["claude"]\nticket_prefix = "eng-"\nevidence_dir = ".evidence"\n' + extra
    )


def landed_worktree(primary: Path, name: str, push: bool = True) -> Path:
    """A clean worktree whose branch was fast-forwarded into origin/main."""
    path = add_worktree(primary, primary.parent / "wt", f"claude/{name}", merge=True)
    git(primary, "push", "-q", "origin", "main")
    if push:
        git(path, "push", "-q", "-u", "origin", f"claude/{name}")
    return path


def stack(primary: Path) -> tuple[Path, Path, str]:
    """A and B pushed, B on top of A; A is squash-merged into origin/main."""
    wt = primary.parent / "wt"
    a = add_worktree(primary, wt, "claude/eng-20-a", merge=False)
    git(a, "push", "-q", "-u", "origin", "claude/eng-20-a")
    b = wt / "eng-21-b"
    git(primary, "worktree", "add", "-q", "-b", "claude/eng-21-b", str(b), "claude/eng-20-a")
    (b / "b.txt").write_text("b\n")
    git(b, "add", ".")
    git(b, "commit", "-q", "-m", "b")
    git(b, "push", "-q", "-u", "origin", "claude/eng-21-b")
    land(primary, "eng-20-a.txt", "squashed\n")  # conflicts with A's file: not content-integrated
    git(primary, "push", "-q", "origin", "main")
    return a, b, git(a, "rev-parse", "HEAD").strip()


def branches(primary: Path) -> list[str]:
    return git(primary, "branch", "--format=%(refname:short)").split()


def test_worktree_with_deleted_directory_is_skipped(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = landed_worktree(primary, "eng-37-gone")
    shutil.rmtree(gone)
    paths = [w["path"] for w in report(capsys)["worktrees"]]  # type: ignore[attr-defined]
    assert not any(path.endswith("eng-37-gone") for path in paths)
    out = cleanup(primary)
    assert out[0].startswith("sweep: ") and not any("eng-37-gone" in line for line in out[1:])
    assert "claude/eng-37-gone" in branches(primary)


def test_cleanup_without_work_to_do_is_one_exact_line(primary: Path) -> None:
    (primary / ".git" / "info" / "exclude").write_text("ignored.txt\n")
    (primary.parent / "wt" / "eng-1-clean" / "ignored.txt").write_text("not dirty\n")
    assert cleanup(primary) == [
        "sweep: cleaned 0, landed 2, dependents 0, blocked 2, unverified 3, orphans 0",
        "blocked claude/eng-1-clean wt/eng-1-clean: unpushed",
        "blocked claude/eng-2-dirty wt/eng-2-dirty: dirty",
    ]


def test_cleanup_removes_squash_landed_bottom_of_stack_and_hints_twice(
    primary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, tip = stack(primary)
    gh = fake_github(primary, tmp_path, monkeypatch)
    gh.set({"claude/eng-20-a": [pr(tip)]})
    hint = (
        "dependent claude/eng-21-b of claude/eng-20-a:"
        f" git rebase --onto main {tip[:12]} claude/eng-21-b"
    )
    out = cleanup(primary)
    assert out[0] == (
        "sweep: cleaned 1, landed 3, dependents 1, blocked 2, unverified 4, orphans 0"
        ", origin stale"
    )
    assert "cleaned claude/eng-20-a wt/eng-20-a (local branch kept)" in out
    assert hint in out
    assert not a.exists() and b.exists()
    assert "claude/eng-20-a" in branches(primary)
    again = cleanup(primary)  # A is now a kept branch; B still needs the hint
    assert hint in again and not any(line.startswith("cleaned") for line in again)
    assert again[0].startswith("sweep: cleaned 0, landed 3, dependents 1,")


def test_landed_pr_worktree_whose_remote_branch_is_gone_is_cleaned(
    primary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _, tip = stack(primary)
    git(primary, "push", "-q", "origin", "--delete", "claude/eng-20-a")
    git(primary, "fetch", "-q", "--prune", "origin")
    gh = fake_github(primary, tmp_path, monkeypatch)
    gh.set({"claude/eng-20-a": [pr(tip)]})
    out = cleanup(primary)
    assert "cleaned claude/eng-20-a wt/eng-20-a (local branch kept)" in out
    assert not a.exists()


def test_cleanup_keeps_blocked_and_prototype_worktrees(
    primary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (primary / ".git" / "info" / "exclude").write_text(".evidence/\n.env\n")
    dirty = landed_worktree(primary, "eng-30-dirty")
    (dirty / "scratch.txt").write_text("x\n")
    local = landed_worktree(primary, "eng-31-local", push=False)
    evidence = landed_worktree(primary, "eng-32-evidence")
    (evidence / ".evidence").mkdir()
    (evidence / ".evidence" / "note.md").write_text("keep\n")
    proto = landed_worktree(primary, "eng-33-capture")
    git(proto, "branch", "-m", "prototype/eng-33-capture")
    git(proto, "push", "-q", "-u", "origin", "prototype/eng-33-capture")
    ignored = landed_worktree(primary, "eng-35-ignored")
    (ignored / ".env").write_text("TOKEN=1\n")
    diverged = add_worktree(primary, primary.parent / "wt", "claude/eng-34-diverged", merge=False)
    land(primary, "eng-34-diverged.txt", "main\n")
    git(primary, "push", "-q", "origin", "main")
    gh = fake_github(primary, tmp_path, monkeypatch)
    gh.set({"claude/eng-34-diverged": [pr("0" * 40)]})
    out = cleanup(primary)
    for line in (
        "blocked claude/eng-30-dirty wt/eng-30-dirty: dirty",
        "blocked claude/eng-31-local wt/eng-31-local: unpushed",
        "blocked claude/eng-32-evidence wt/eng-32-evidence: evidence",
        "blocked claude/eng-34-diverged wt/eng-34-diverged: tip diverged",
        "cleaned claude/eng-35-ignored wt/eng-35-ignored (local branch kept)",
    ):
        assert line in out
    assert not any("prototype/" in line for line in out[1:])
    unmerged = primary.parent / "wt" / "eng-3-open"
    for path in (dirty, local, evidence, proto, diverged, unmerged):
        assert path.exists(), path
    assert not ignored.exists()  # ignored files are disposable


@pytest.mark.parametrize("mode", ["report", "keep"])
def test_report_and_keep_modes_remove_nothing(
    primary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    a, b, tip = stack(primary)
    gh = fake_github(primary, tmp_path, monkeypatch)
    gh.set({"claude/eng-20-a": [pr(tip)]})
    settings(primary, f'cleanup = "{mode}"\n')
    out = cleanup(primary)
    assert a.exists() and b.exists()
    assert not any(line.startswith("cleaned") for line in out)
    if mode == "report":
        assert "landed claude/eng-20-a wt/eng-20-a: would remove" in out
        assert any(line.startswith("dependent claude/eng-21-b of ") for line in out)
    else:
        assert not any(line.startswith(("landed", "dependent", "blocked")) for line in out)


def test_fetch_failure_marks_origin_stale_and_still_classifies(primary: Path) -> None:
    git(primary, "remote", "set-url", "origin", str(primary.parent / "gone.git"))
    out = cleanup(primary)
    assert out[0].endswith(", origin stale")
    assert "landed 2" in out[0]
    assert "blocked claude/eng-1-clean wt/eng-1-clean: unpushed" in out


def test_cleanup_skips_a_primary_off_the_primary_branch(primary: Path) -> None:
    git(primary, "checkout", "-q", "-b", "claude/eng-50-x")
    assert cleanup(primary) == ["sweep: primary checkout is not on main; skipped"]


def test_bad_config_prints_in_cleanup_and_blocks_the_gate(primary: Path) -> None:
    settings(primary, "bogus = 1\n")
    assert cleanup(primary)[0].startswith("lumberjack: unknown key: bogus")
    gate = sweep(primary, "--gate")
    assert gate.returncode == 2 and "unknown key: bogus" in gate.stderr


def test_stop_gate_false_silences_the_gate(primary: Path) -> None:
    git(primary, "checkout", "-q", "-b", "Feature_X")
    assert sweep(primary, "--gate").returncode == 2
    settings(primary, "stop_gate = false\n")
    gate = sweep(primary, "--gate")
    assert (gate.returncode, gate.stdout, gate.stderr) == (0, "", "")


def config_value(primary: Path, key: str) -> str:
    done = subprocess.run(
        ["git", "config", "--get", key], cwd=primary, capture_output=True, text=True
    )
    return done.stdout.strip()


def test_current_checkout_is_never_removed(primary: Path) -> None:
    path = landed_worktree(primary, "eng-30-here")
    done = sweep(primary, "--cleanup", cwd=path)
    assert done.returncode == 0, done.stderr
    assert "blocked claude/eng-30-here wt/eng-30-here: current checkout" in done.stdout.splitlines()
    assert path.exists()


def test_fresh_worktree_is_kept_until_it_has_its_own_commits(primary: Path) -> None:
    wt = primary.parent / "wt"
    fresh, merged = wt / "eng-40-fresh", wt / "eng-41-merged"
    for path in (fresh, merged):  # tracks origin/main: looks integrated and pushed
        git(primary, "worktree", "add", "-q", "-b", f"claude/{path.name}", str(path), "origin/main")
    (merged / "m.txt").write_text("m\n")
    git(merged, "add", ".")
    git(merged, "commit", "-q", "-m", "m")
    git(merged, "push", "-q", "-u", "origin", "claude/eng-41-merged")
    git(primary, "merge", "-q", "--ff-only", "claude/eng-41-merged")
    git(primary, "push", "-q", "origin", "main")
    out = cleanup(primary)
    assert "blocked claude/eng-40-fresh wt/eng-40-fresh: fresh" in out
    assert "cleaned claude/eng-41-merged wt/eng-41-merged (local branch kept)" in out
    assert fresh.exists() and not merged.exists()
    assert remove(primary, fresh) == (1, ["blocked claude/eng-40-fresh wt/eng-40-fresh: fresh"])
    (primary / ".git" / "logs" / "refs" / "heads" / "claude" / "eng-40-fresh").unlink()
    assert "blocked claude/eng-40-fresh wt/eng-40-fresh: fresh" in cleanup(primary)  # no reflog
    added = primary / ".git" / "worktrees" / "eng-40-fresh" / "commondir"
    old = added.stat().st_mtime - sweep_worktrees.FRESH_HOURS * 3600 - 60
    os.utime(added, (old, old))
    assert "cleaned claude/eng-40-fresh wt/eng-40-fresh (local branch kept)" in cleanup(primary)


def test_locked_worktree_is_never_removed(primary: Path) -> None:
    path = landed_worktree(primary, "eng-42-locked")
    git(primary, "worktree", "lock", "--reason", "busy", str(path))
    assert "blocked claude/eng-42-locked wt/eng-42-locked: locked" in cleanup(primary)
    assert remove(primary, path) == (1, ["blocked claude/eng-42-locked wt/eng-42-locked: locked"])
    assert path.exists()


def test_self_heal_sets_script_and_hooks_path_only_when_free(primary: Path) -> None:
    cleanup(primary)
    assert config_value(primary, "lumberjack.script") == SCRIPT.as_posix()
    assert config_value(primary, "core.hooksPath") == ""
    (primary / ".githooks").mkdir()
    (primary / ".githooks" / "post-merge").write_text("#!/bin/sh\n")
    cleanup(primary)
    assert config_value(primary, "core.hooksPath") == ""  # no lumberjack marker
    (primary / ".githooks" / "post-merge").write_text("#!/bin/sh\n# >>> lumberjack\n")
    cleanup(primary)
    assert config_value(primary, "core.hooksPath") == ".githooks"
    git(primary, "config", "core.hooksPath", "custom")
    cleanup(primary)
    assert config_value(primary, "core.hooksPath") == "custom"


@pytest.mark.parametrize(
    ("name", "text", "manager"),
    [
        (".husky", None, "husky"),
        ("lefthook.yml", "", "lefthook"),
        (".lefthook.yml", "", "lefthook"),
        (".pre-commit-config.yaml", "", "pre-commit"),
        ("package.json", '{"simple-git-hooks": {}}', "simple-git-hooks"),
    ],
)
def test_hook_manager_gets_a_hint_until_wired_and_no_hooks_path(
    primary: Path, name: str, text: str | None, manager: str
) -> None:
    (primary / ".githooks").mkdir()
    (primary / ".githooks" / "post-merge").write_text("#!/bin/sh\n")
    if text is None:
        (primary / name).mkdir()
    else:
        (primary / name).write_text(text)
    hint = f"hint: post-merge hook managed by {manager}; run /lumberjack:setup to add lumberjack"
    assert cleanup(primary)[-1] == hint
    assert config_value(primary, "lumberjack.script") == SCRIPT.as_posix()
    assert config_value(primary, "core.hooksPath") == ""
    (primary / "lefthook-local.yml").write_text("# lumberjack.script wired by setup\n")
    assert hint not in cleanup(primary)


def test_paths_are_relative_to_the_primary_parent_else_home(
    primary: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, other = tmp_path_factory.mktemp("home"), tmp_path_factory.mktemp("other")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    short_path = sweep_worktrees.short_path
    assert short_path(primary, primary.parent / "wt" / "x") == "wt/x"
    assert short_path(primary, home / "wt" / "y") == "~/wt/y"
    assert short_path(primary, other / "z") == (other / "z").resolve().as_posix()


def remove(primary: Path, target: Path, cwd: Path | None = None) -> tuple[int, list[str]]:
    done = sweep(primary, "--remove", str(target), cwd=cwd)
    return done.returncode, done.stdout.splitlines()


def test_remove_takes_only_the_named_worktree_and_keeps_its_branch(primary: Path) -> None:
    wt = primary.parent / "wt"
    (primary / ".git" / "info" / "exclude").write_text("__pycache__/\n")
    (wt / "eng-3-open" / "__pycache__").mkdir()
    (wt / "eng-3-open" / "__pycache__" / "x.pyc").write_text("")  # caches do not block
    assert remove(primary, wt / "eng-3-open") == (
        0, ["cleaned claude/eng-3-open wt/eng-3-open (local branch kept)"]
    )
    assert not (wt / "eng-3-open").exists()
    assert "claude/eng-3-open" in branches(primary)
    # eng-1 is landed and clean, yet unnamed: the plain sweep would remove it, --remove must not.
    assert (wt / "eng-1-clean").exists() and (wt / "eng-2-dirty").exists()


def test_remove_refuses_a_blocked_worktree_with_its_reason(primary: Path) -> None:
    wt = primary.parent / "wt"
    dirty, open_ = wt / "eng-2-dirty", wt / "eng-3-open"
    assert remove(primary, dirty) == (1, ["blocked claude/eng-2-dirty wt/eng-2-dirty: dirty"])
    assert remove(primary, open_, cwd=open_) == (
        1, ["blocked claude/eng-3-open wt/eng-3-open: current checkout"]
    )
    assert remove(primary, primary) == (1, ["blocked main primary: primary checkout"])
    assert remove(primary, wt / "nowhere") == (1, [f"blocked {wt / 'nowhere'}: not a worktree"])
    (primary / ".git" / "info" / "exclude").write_text(".env\n")
    (open_ / ".env").write_text("SECRET=1\n")
    assert remove(primary, open_) == (
        1, ["blocked claude/eng-3-open wt/eng-3-open: ignored files"]
    )
    proto = wt / "proto"
    git(primary, "worktree", "add", "-q", str(proto), "prototype/eng-65-demo")
    assert remove(primary, proto) == (1, ["blocked prototype/eng-65-demo wt/proto: prototype"])
    detached = wt / "detached"
    git(primary, "worktree", "add", "-q", "--detach", str(detached))
    (detached / "d.txt").write_text("d\n")
    git(detached, "add", ".")
    git(detached, "commit", "-q", "-m", "detached")
    assert remove(primary, detached) == (1, ["blocked (detached) wt/detached: no branch"])
    assert dirty.exists() and open_.exists() and proto.exists() and detached.exists()


def test_remove_takes_a_detached_worktree_whose_commit_is_on_a_branch(primary: Path) -> None:
    detached = primary.parent / "wt" / "detached"
    git(primary, "worktree", "add", "-q", "--detach", str(detached), "claude/eng-3-open")
    assert remove(primary, detached) == (0, ["cleaned (detached) wt/detached"])
    assert not detached.exists()


def failing_remove(monkeypatch: pytest.MonkeyPatch, unregister: bool) -> None:
    """git worktree remove fails as on a locked Windows directory, after it did its work or not."""
    real = sweep_worktrees.git

    def fake(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:2] != ("worktree", "remove"):
            return real(cwd, *args)
        if unregister:
            real(cwd, *args)
            Path(args[2]).mkdir()
        return subprocess.CompletedProcess(["git", *args], 255, "", "error: Permission denied\n")

    monkeypatch.setattr(sweep_worktrees, "git", fake)


def test_remove_reports_a_half_failed_removal_as_cleaned(
    primary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_ = primary.parent / "wt" / "eng-3-open"
    failing_remove(monkeypatch, unregister=True)
    assert sweep_worktrees.remove_named(primary, str(open_)) == (
        True, "cleaned claude/eng-3-open wt/eng-3-open (local branch kept; directory left)"
    )
    assert open_.exists() and not any(open_.iterdir())


def test_remove_reports_a_failed_removal_as_blocked(
    primary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_ = primary.parent / "wt" / "eng-3-open"
    failing_remove(monkeypatch, unregister=False)
    assert sweep_worktrees.remove_named(primary, str(open_)) == (
        False, "blocked claude/eng-3-open wt/eng-3-open: error: Permission denied"
    )
