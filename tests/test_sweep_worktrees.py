"""Sweep classification and pruning against a throwaway origin and clone."""

import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import REAL_WHICH
from helpers import git, write

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
    assert out[0].endswith("orphans 4, docs 0")
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
        "sweep: cleaned 0, landed 2, dependents 0, blocked 2, unverified 3, orphans 0, docs 0",
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
        "sweep: cleaned 1, landed 3, dependents 1, blocked 2, unverified 4, orphans 0, docs 0"
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


def doc_worktree(primary: Path, name: str, doc: str | None, extra: str = 'doc_roots = ["docs/work"]\n') -> Path:
    """A landed worktree, optionally holding an ignored file at doc, with doc_roots configured."""
    (primary / ".git" / "info" / "exclude").write_text("docs/work/\n.evidence/\n")
    settings(primary, extra)
    path = landed_worktree(primary, name)
    if doc:
        (path / doc).parent.mkdir(parents=True, exist_ok=True)
        (path / doc).write_text("keep\n")
    return path


def test_doc_root_file_keeps_a_landed_worktree_in_cleanup_and_remove(primary: Path) -> None:
    path = doc_worktree(primary, "eng-40-docs", "docs/work/eng-40-docs/plan.md")
    assert "blocked claude/eng-40-docs wt/eng-40-docs: docs" in cleanup(primary)
    assert path.exists()
    assert remove(primary, path) == (1, ["blocked claude/eng-40-docs wt/eng-40-docs: docs"])
    assert path.exists()


def test_empty_or_absent_doc_root_does_not_keep_a_worktree(primary: Path) -> None:
    absent = doc_worktree(primary, "eng-41-absent", None)
    empty = landed_worktree(primary, "eng-42-empty")
    (empty / "docs" / "work" / "eng-42-empty").mkdir(parents=True)  # directories hold no document
    out = cleanup(primary)
    assert "cleaned claude/eng-41-absent wt/eng-41-absent (local branch kept)" in out
    assert "cleaned claude/eng-42-empty wt/eng-42-empty (local branch kept)" in out
    assert not absent.exists() and not empty.exists()


def test_tracked_files_in_a_doc_root_do_not_keep_a_worktree(primary: Path) -> None:
    path = doc_worktree(primary, "eng-43-tracked", None)
    (primary / ".git" / "info" / "exclude").write_text("")
    docs = add_worktree(primary, primary.parent / "wt", "claude/eng-44-tracked", merge=True)
    (docs / "docs" / "work").mkdir(parents=True)
    (docs / "docs" / "work" / "guide.md").write_text("committed\n")
    git(docs, "add", ".")
    git(docs, "commit", "-q", "-m", "docs")
    git(primary, "merge", "-q", "--ff-only", "claude/eng-44-tracked")
    git(primary, "push", "-q", "origin", "main")
    git(docs, "push", "-q", "-u", "origin", "claude/eng-44-tracked")
    out = cleanup(primary)
    assert "cleaned claude/eng-44-tracked wt/eng-44-tracked (local branch kept)" in out
    assert not docs.exists() and not path.exists()


def test_evidence_dir_still_reports_evidence_beside_doc_roots(primary: Path) -> None:
    path = doc_worktree(primary, "eng-45-evidence", ".evidence/receipt.txt")
    assert "blocked claude/eng-45-evidence wt/eng-45-evidence: evidence" in cleanup(primary)
    assert remove(primary, path) == (
        1, ["blocked claude/eng-45-evidence wt/eng-45-evidence: evidence"]
    )


def docs_by_path(primary: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, object]]:
    return {d["path"]: d for d in report(capsys)["docs"]}  # type: ignore[attr-defined]


def test_doc_bundles_are_untracked_entries_with_tickets_class_and_state(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/guide.md", "Ticket: eng-70\n")  # tracked: never a bundle
    write(primary, "docs/work/agents/index.md", "tracked\n")
    git(primary, "add", "docs")
    git(primary, "commit", "-q", "-m", "guide")
    head = "Tickets: eng-91, eng-92\n"
    write(primary, "docs/work/eng-90-handoff/notes.md", head)
    write(primary, "docs/work/eng-90-handoff/pad.bin", "x" * (1024 - len(head)))
    write(primary, "docs/work/eng-3-plan/plan.md", "x")  # a worktree carries eng-3
    write(primary, "docs/work/research-notes/README.md", "# R\n\n**Ticket:** eng-95\n")
    write(primary, "docs/work/loose/x.md", "no ticket here\n")
    write(primary, "docs/work/eng-96-note.md", "x" * 2048)
    write(primary, "docs/work/agents/eng-97-plan/plan.md", "x")  # below a tracked folder
    docs = docs_by_path(primary, capsys)
    assert "docs/work/guide.md" not in docs
    got = {p: (d["class"], d["state"], d["tickets"]) for p, d in docs.items()}
    assert got == {
        "docs/work/eng-90-handoff": ("ephemeral", "linked", ["eng-90", "eng-91", "eng-92"]),
        "docs/work/eng-3-plan": ("ephemeral", "live", ["eng-3"]),
        "docs/work/research-notes": ("reference", "linked", ["eng-95"]),
        "docs/work/loose": ("ephemeral", "unlinked", []),
        "docs/work/eng-96-note.md": ("ephemeral", "linked", ["eng-96"]),
        "docs/work/agents/eng-97-plan": ("ephemeral", "linked", ["eng-97"]),
    }
    out = cleanup(primary)
    assert out[0].endswith("orphans 0, docs 0")  # no strategy: the lines only report
    assert "doc ephemeral docs/work/eng-90-handoff: tickets eng-90, eng-91, eng-92; 1.0 KB" in out
    assert "doc reference docs/work/research-notes: tickets eng-95; 24 B" in out
    assert "doc ephemeral docs/work/eng-96-note.md: tickets eng-96; 2.0 KB" in out
    assert not any("eng-3-plan" in line or "loose" in line for line in out if line.startswith("doc"))
    assert (primary / "docs/work/eng-90-handoff/notes.md").exists()  # nothing is deleted


def test_wholly_untracked_doc_root_lists_its_children(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/eng-80-a/x.md", "x")
    write(primary, "docs/work/eng-81-b/x.md", "x")
    assert sorted(docs_by_path(primary, capsys)) == ["docs/work/eng-80-a", "docs/work/eng-81-b"]


def test_doc_roots_and_doc_reference_keys_match_in_any_spelling(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["./docs/work"]' + "\n"
             + 'doc_reference = {"docs/work/" = ["spec"]}' + "\n")
    write(primary, "docs/work/eng-82-spec/a.md", "x")
    assert docs_by_path(primary, capsys)["docs/work/eng-82-spec"]["class"] == "reference"


def test_detached_worktree_folder_name_makes_a_bundle_live(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/spec/a.md", "Ticket: eng-97\n")
    write(primary, "docs/work/other/a.md", "Ticket: eng-98\n")
    write(primary, "docs/work/none/a.md", "Ticket: eng-99\n")
    git(primary, "worktree", "add", "-q", "--detach", str(primary.parent / "wt" / "eng-97-codex"))
    git(primary, "branch", "claude/eng-99-landed")  # its tip is on main: landed
    git(primary, "checkout", "-q", "-b", "claude/eng-98-later")
    write(primary, "later.txt", "x")
    git(primary, "add", "later.txt")
    git(primary, "commit", "-q", "-m", "unlanded")
    git(primary, "checkout", "-q", "main")
    states = {p: d["state"] for p, d in docs_by_path(primary, capsys).items()}
    assert states == {  # detached worktree folder, unlanded branch, landed branch only
        "docs/work/spec": "live",
        "docs/work/other": "live",
        "docs/work/none": "linked",
    }


def merge_to_main(primary: Path, branch: str) -> None:
    git(primary, "merge", "-q", "--no-edit", branch)
    git(primary, "push", "-q", "origin", "main")


def test_two_ticket_bundle_stays_live_across_the_first_ticket_landing(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/eng-100-research/notes.md", "Tickets: eng-101\n")
    wt = primary.parent / "wt"
    first = add_worktree(primary, wt, "claude/eng-100-first", merge=False)
    second = add_worktree(primary, wt, "claude/eng-101-second", merge=False)
    bundle = "docs/work/eng-100-research"
    assert docs_by_path(primary, capsys)[bundle]["state"] == "live"
    merge_to_main(primary, "claude/eng-100-first")  # the first ticket lands; the second is open
    git(primary, "worktree", "remove", "--force", str(first))
    assert docs_by_path(primary, capsys)[bundle]["state"] == "live"
    assert not any(line.startswith("doc ") for line in cleanup(primary))
    merge_to_main(primary, "claude/eng-101-second")
    git(primary, "worktree", "remove", "--force", str(second))
    assert docs_by_path(primary, capsys)[bundle]["state"] == "linked"


def test_bundle_named_in_a_tracked_document_is_live(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/eng-110-plan/a.md", "Ticket: eng-110\n")
    write(primary, "docs/work/eng-111-plan/a.md", "Ticket: eng-111\n")
    write(primary, "docs/work/scratch/a.md", "no ticket\n")
    write(primary, "docs/work/quiet/a.md", "no ticket\n")
    write(primary, "docs/work/eng-112-spec/a.md", "x")
    states = {d["state"] for d in docs_by_path(primary, capsys).values()}
    assert states == {"linked", "unlinked"}
    write(primary, "guide.md", "Background: docs/work/eng-110-plan and docs/work/scratch.\n"
          "See eng-112-spec; keep it quiet.\n")  # a bare name counts only with a ticket in it
    git(primary, "add", "guide.md")  # tracked, uncommitted: git grep reads the index
    states = {p: d["state"] for p, d in docs_by_path(primary, capsys).items()}
    assert states == {
        "docs/work/eng-110-plan": "live",
        "docs/work/eng-111-plan": "linked",
        "docs/work/scratch": "live",
        "docs/work/quiet": "unlinked",
        "docs/work/eng-112-spec": "live",
    }


def test_bundle_named_in_an_unlanded_branch_commit_message_is_live(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    for name in ("open-spec", "landed-spec", "unseen-spec"):
        write(primary, f"docs/work/eng-120-{name}/a.md", "Ticket: eng-129\n")
    for branch, name in (("claude/eng-121-x", "open-spec"), ("claude/eng-122-y", "landed-spec")):
        path = primary.parent / "wt" / branch.split("/")[1]
        git(primary, "worktree", "add", "-q", "-b", branch, str(path))
        write(path, f"{path.name}.txt", "x")
        git(path, "add", ".")
        git(path, "commit", "-q", "-m", f"Build on docs/work/eng-120-{name}")
    merge_to_main(primary, "claude/eng-122-y")
    git(primary, "worktree", "remove", "--force", str(primary.parent / "wt" / "eng-121-x"))
    states = {p.split("/")[-1]: d["state"] for p, d in docs_by_path(primary, capsys).items()}
    assert states == {  # the open branch outlives its worktree and keeps its message live
        "eng-120-open-spec": "live",
        "eng-120-landed-spec": "linked",
        "eng-120-unseen-spec": "linked",
    }


def test_worktrees_inside_a_doc_root_are_not_bundles(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/eng-60-plan/a.md", "x")
    git(primary, "worktree", "add", "-q", "--detach", str(primary / "docs/work/eng-61-wt"))
    git(primary, "worktree", "add", "-q", "--detach", str(primary / "docs/work/trees/eng-62-wt"))
    assert sorted(docs_by_path(primary, capsys)) == ["docs/work/eng-60-plan"]


def test_doc_reference_overrides_the_class_words_per_root(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings(
        primary,
        'doc_roots = ["docs/work", "notes"]\n[doc_reference]\n"docs/work" = ["spike"]\n',
    )
    for path in ("docs/work/research-a", "docs/work/spike-b", "notes/research-c"):
        write(primary, f"{path}/x.md", "x")
    classes = {p: d["class"] for p, d in docs_by_path(primary, capsys).items()}
    assert classes == {
        "docs/work/research-a": "ephemeral",
        "docs/work/spike-b": "reference",
        "notes/research-c": "reference",
    }


def test_doc_sizes_stay_fast_on_a_large_tree_and_degrade_when_over_budget(
    primary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    for bundle in range(3):
        for sub in range(10):
            for n in range(100):
                write(primary, f"docs/work/eng-5{bundle}-x/s{sub}/f{n}.txt", "abcd")
    cfg = sweep_worktrees.load_config(primary)
    trees = sweep_worktrees.registered(primary)
    start = time.monotonic()
    rows = sweep_worktrees.doc_rows(primary, cfg, trees, {})
    assert time.monotonic() - start < 5
    assert [r["size"] for r in rows] == [4000] * 3
    monkeypatch.setattr(sweep_worktrees, "DOC_SECONDS", -1)
    rows = sweep_worktrees.doc_rows(primary, cfg, trees, {})
    assert [r["size"] for r in rows] == [None] * 3
    assert "size unknown" in sweep_worktrees.doc_line(rows[0])


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


def test_branch_reset_to_its_creation_commit_is_not_fresh(primary: Path) -> None:
    path = primary.parent / "wt" / "eng-43-reset"
    git(primary, "worktree", "add", "-q", "-b", "claude/eng-43-reset", str(path), "origin/main")
    (path / "r.txt").write_text("r")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "r")
    git(path, "reset", "-q", "--hard", "origin/main")
    assert "cleaned claude/eng-43-reset wt/eng-43-reset (local branch kept)" in cleanup(primary)


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


def test_the_sweep_and_remove_both_keep_the_primary_and_prototypes(primary: Path) -> None:
    cfg = sweep_worktrees.load_config(primary)
    proto = primary.parent / "wt" / "proto"
    git(primary, "worktree", "add", "-q", str(proto), "prototype/eng-65-demo")
    for path, branch, reason in (
        (primary, "main", "primary checkout"),
        (proto, "prototype/eng-65-demo", "prototype"),
    ):
        row = {"path": str(path), "branch": branch, "head": "", "dirty": 0, "tier": "integrated"}
        for explicit in (False, True):
            assert sweep_worktrees.block_reason(primary, cfg, row, None, explicit=explicit) == reason


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


STRATEGY = {"scope": "bundle", "trash_days": 30, "grace_days": 0}


def strategy_settings(primary: Path, **override: object) -> None:
    table = ", ".join(f"{k} = {json.dumps(v)}" for k, v in {**STRATEGY, **override}.items())
    settings(primary, f'doc_roots = ["docs/work"]\ndoc_strategy = {{{table}}}\n')


def clear(primary: Path, target: str, *args: str) -> tuple[int, str]:
    done = sweep(primary, "--clear", target, *args)
    return done.returncode, done.stdout.strip()


def trash_folders(primary: Path) -> list[Path]:
    folder = primary / ".git" / "lumberjack-trash"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_clear_removes_nothing_until_a_strategy_is_saved(primary: Path) -> None:
    settings(primary, 'doc_roots = ["docs/work"]\n')
    write(primary, "docs/work/eng-130-handoff/a.md", "Ticket: eng-130\n")
    assert clear(primary, "docs/work/eng-130-handoff") == (
        1, "blocked docs/work/eng-130-handoff: no strategy saved"
    )
    assert (primary / "docs/work/eng-130-handoff/a.md").exists() and not trash_folders(primary)


def test_clear_trashes_a_linked_bundle_with_a_timestamp(primary: Path) -> None:
    strategy_settings(primary)
    write(primary, "docs/work/eng-131-handoff/a.md", "Ticket: eng-131\n")
    write(primary, "docs/work/eng-131-handoff/run/out.log", "log")
    code, line = clear(primary, "docs/work/eng-131-handoff")
    (folder,) = trash_folders(primary)
    assert (code, line) == (0, f"cleared docs/work/eng-131-handoff: trashed {folder.name}")
    assert time.strptime(folder.name, sweep_worktrees.TRASH_STAMP)
    assert not (primary / "docs/work/eng-131-handoff").exists()
    assert (folder / "docs/work/eng-131-handoff/run/out.log").read_text() == "log"


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("eng-3-handoff", "live"),  # the open eng-3 worktree carries it
        ("scratch", "unlinked"),
    ],
)
def test_clear_refuses_live_and_unlinked_bundles(primary: Path, name: str, reason: str) -> None:
    strategy_settings(primary)
    write(primary, f"docs/work/{name}/a.md", "x")
    assert clear(primary, f"docs/work/{name}") == (1, f"blocked docs/work/{name}: {reason}")
    assert (primary / f"docs/work/{name}/a.md").exists() and not trash_folders(primary)
    assert clear(primary, "docs/work/missing")[1] == "blocked docs/work/missing: not a bundle"


def test_clear_with_markdown_scope_trashes_only_markdown(primary: Path) -> None:
    strategy_settings(primary, scope="markdown")
    write(primary, "docs/work/eng-133-draft/a.md", "Ticket: eng-133\n")
    write(primary, "docs/work/eng-133-draft/deep/b.MD", "x")
    write(primary, "docs/work/eng-133-draft/out.log", "keep")
    code, line = clear(primary, "docs/work/eng-133-draft")
    (folder,) = trash_folders(primary)
    assert (code, line) == (0, f"cleared docs/work/eng-133-draft: trashed {folder.name}")
    left = [p.name for p in (primary / "docs/work/eng-133-draft").rglob("*") if p.is_file()]
    assert left == ["out.log"]
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == ["a.md", "b.MD"]


def link_dir(link: Path, target: Path) -> None:
    """A directory link: a junction on Windows (no privilege needed), else a symlink."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def outside_dir(primary: Path) -> Path:
    outside = primary.parent / "outside"
    write(outside, "keep.md", "x")
    write(outside, "sub/deep.md", "x")
    return outside


@pytest.mark.parametrize("scope", ["markdown", "bundle"])
def test_clear_refuses_a_bundle_that_is_a_link_out_of_the_repository(
    primary: Path, scope: str
) -> None:
    strategy_settings(primary, scope=scope)
    outside = outside_dir(primary)
    link_dir(primary / "docs/work/eng-136-notes", outside)
    assert clear(primary, "docs/work/eng-136-notes") == (
        1, "blocked docs/work/eng-136-notes: links outside the repository"
    )
    assert sorted(p.name for p in outside.rglob("*.md")) == ["deep.md", "keep.md"]
    assert os.access(outside / "keep.md", os.W_OK)


def test_clear_refuses_a_link_nested_in_a_markdown_scope_bundle(primary: Path) -> None:
    strategy_settings(primary, scope="markdown")
    outside = outside_dir(primary)
    write(primary, "docs/work/eng-136-notes/own.md", "Ticket: eng-136\n")
    link_dir(primary / "docs/work/eng-136-notes/linked", outside)
    assert clear(primary, "docs/work/eng-136-notes") == (
        1, "blocked docs/work/eng-136-notes: links outside the repository"
    )
    assert sorted(p.name for p in outside.rglob("*.md")) == ["deep.md", "keep.md"]
    assert (primary / "docs/work/eng-136-notes/own.md").exists() and not trash_folders(primary)


def test_clearing_a_whole_bundle_drops_a_nested_link_but_not_its_target(primary: Path) -> None:
    strategy_settings(primary, scope="bundle")
    outside = outside_dir(primary)
    write(primary, "docs/work/eng-136-notes/own.md", "Ticket: eng-136\n")
    link_dir(primary / "docs/work/eng-136-notes/linked", outside)
    assert clear(primary, "docs/work/eng-136-notes")[0] == 0
    assert not (primary / "docs/work/eng-136-notes").exists()
    assert sorted(p.name for p in outside.rglob("*.md")) == ["deep.md", "keep.md"]


def test_clear_with_markdown_scope_blocks_when_nothing_is_in_scope(primary: Path) -> None:
    strategy_settings(primary, scope="markdown")
    write(primary, "docs/work/eng-137-out.json", "Ticket: eng-137\n")
    write(primary, "docs/work/eng-138-logs/run.log", "Ticket: eng-138\n")
    for name in ("eng-137-out.json", "eng-138-logs"):
        assert clear(primary, f"docs/work/{name}") == (
            1, f"blocked docs/work/{name}: no Markdown in scope"
        )
        assert (primary / "docs/work" / name).exists()
    assert not trash_folders(primary)


def test_keep_root_keeps_a_bundle_the_rule_would_trash(primary: Path) -> None:
    settings(
        primary,
        'doc_roots = ["docs/work", "docs/handoffs"]\n'
        'doc_strategy = {scope = "bundle", trash_days = 30, grace_days = 0, roots = {"./docs/handoffs/" = "keep"}}\n',
    )
    write(primary, "docs/handoffs/eng-139-a/a.md", "Ticket: eng-139\n")
    write(primary, "docs/work/eng-139-b/a.md", "Ticket: eng-139\n")
    assert clear(primary, "docs/handoffs/eng-139-a") == (
        1, "blocked docs/handoffs/eng-139-a: kept by strategy"
    )
    assert clear(primary, "docs/work/eng-139-b")[0] == 0
    assert (primary / "docs/handoffs/eng-139-a/a.md").exists()


def test_each_doc_row_names_the_action_its_strategy_gives_it(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(primary, "docs/work/eng-142-handoff/a.md", "Ticket: eng-142\n")
    write(primary, "docs/work/eng-143-research/a.md", "Ticket: eng-143\n")
    write(primary, "docs/handoffs/eng-144-a/a.md", "Ticket: eng-144\n")
    write(primary, "docs/lab/eng-145-research/a.md", "Ticket: eng-145\n")
    settings(primary, 'doc_roots = ["docs/work", "docs/handoffs", "docs/lab"]\n')
    assert {d["action"] for d in docs_by_path(primary, capsys).values()} == {""}  # no strategy
    settings(
        primary,
        'doc_roots = ["docs/work", "docs/handoffs", "docs/lab"]\n'
        'doc_strategy = {scope = "bundle", trash_days = 30, grace_days = 0,'
        ' roots = {"docs/handoffs" = "keep", "docs/lab" = "keep"}}\n',
    )
    actions = {p: d["action"] for p, d in docs_by_path(primary, capsys).items()}
    assert actions == {
        "docs/work/eng-142-handoff": "trash",
        "docs/work/eng-143-research": "promote",
        "docs/handoffs/eng-144-a": "keep",  # a keep-root wins over the class action
        "docs/lab/eng-145-research": "keep",
    }


def test_clear_takes_a_group_and_exits_1_when_one_is_blocked(primary: Path) -> None:
    strategy_settings(primary)
    write(primary, "docs/work/eng-140-a/a.md", "Ticket: eng-140\n")
    write(primary, "docs/work/eng-141-b/a.md", "Ticket: eng-141\n")
    code, out = clear(primary, "docs/work/eng-140-a", "docs/work/missing", "docs/work/eng-141-b")
    (folder,) = trash_folders(primary)
    assert (code, out.splitlines()) == (1, [
        f"cleared docs/work/eng-140-a: trashed {folder.name}",
        "blocked docs/work/missing: not a bundle",
        f"cleared docs/work/eng-141-b: trashed {folder.name}",
    ])


def test_reference_bundle_needs_a_pushed_copy_before_it_is_trashed(primary: Path) -> None:
    strategy_settings(primary)
    bundle = "docs/work/eng-134-research"
    write(primary, f"{bundle}/a.md", "Ticket: eng-134\n")
    need = f"blocked {bundle}: promote it first (--promoted)"
    assert clear(primary, bundle) == (1, need)
    gone = "blocked {}: the promoted copy is not on origin".format(bundle)
    assert clear(primary, bundle, "--promoted", "prototype/eng-65-demo") == (1, gone)  # local only
    assert clear(primary, bundle, "--promoted", "findings.md") == (1, gone)
    git(primary, "push", "-q", "origin", "prototype/eng-65-demo")
    git(primary, "fetch", "-q", "origin")
    assert clear(primary, bundle, "--promoted", "prototype/eng-65-demo")[0] == 0
    assert not (primary / bundle).exists() and len(trash_folders(primary)) == 1


def test_reference_bundle_is_cleared_once_a_tracked_doc_is_on_origin(primary: Path) -> None:
    strategy_settings(primary)
    bundle = "docs/work/eng-135-prototype"
    write(primary, f"{bundle}/a.md", "Ticket: eng-135\n")
    write(primary, "findings.md", "promoted\n")
    git(primary, "add", "findings.md")
    git(primary, "commit", "-q", "-m", "findings")
    git(primary, "push", "-q", "origin", "main")
    # Stale origin/main, as when the promotion merged after the session's fetch: --clear fetches.
    git(primary, "update-ref", "refs/remotes/origin/main", "HEAD~1")
    assert clear(primary, bundle, "--promoted", "findings.md")[0] == 0
    settings(primary, 'doc_roots = ["docs/work"]\n'
             'doc_strategy = {scope = "bundle", trash_days = 30, grace_days = 0, roots = {"docs/work" = "keep"}}\n')
    write(primary, f"{bundle}/a.md", "Ticket: eng-135\n")
    assert clear(primary, bundle, "--promoted", "findings.md") == (
        1, f"blocked {bundle}: kept by strategy"
    )


def test_sweep_reports_trash_past_retention_and_only_purge_removes_it(
    primary: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    strategy_settings(primary, trash_days=7)
    trash = primary / ".git" / "lumberjack-trash"
    write(trash, "20200101T000000Z/docs/work/old/a.md", "x")
    fresh = time.strftime(sweep_worktrees.TRASH_STAMP, time.gmtime())
    write(trash, f"{fresh}/docs/work/new/a.md", "x")
    write(trash, "stray/a.md", "not ours")
    out = cleanup(primary)
    assert out[0].endswith("docs 0, trash 1")
    assert any(l.startswith("trash 20200101T000000Z: ") and l.endswith("days old; 1 B") for l in out)
    assert [t["name"] for t in report(capsys)["trash"]] == ["20200101T000000Z"]  # type: ignore[attr-defined]
    assert (trash / "20200101T000000Z").exists()  # reporting never deletes
    done = sweep(primary, "--purge-trash")
    assert done.stdout.splitlines() == ["purged 20200101T000000Z"]
    assert sorted(p.name for p in trash.iterdir()) == sorted([fresh, "stray"])
    assert sweep(primary, "--purge-trash").stdout.strip() == "trash: nothing past retention"


def rule_bundles(
    primary: Path, mode: str = "prune", strategy: bool = True, scope: str = "bundle"
) -> None:
    saved = f'doc_strategy = {{scope = "{scope}", trash_days = 30, grace_days = 0, roots = {{"docs/handoffs" = "keep"}}}}\n'
    settings(
        primary,
        f'cleanup = "{mode}"\ndoc_roots = ["docs/work", "docs/handoffs"]\n' + (saved if strategy else ""),
    )
    write(primary, "docs/work/eng-150-handoff/a.md", "x\n")  # linked, ephemeral
    write(primary, "docs/work/eng-151-research/a.md", "x\n")  # linked, reference
    write(primary, "docs/work/scratch/a.md", "no ticket\n")  # unlinked
    write(primary, "docs/work/eng-3-handoff/a.md", "x\n")  # live: the eng-3 worktree is open
    write(primary, "docs/handoffs/eng-152-a/a.md", "x\n")  # under a keep-root


def test_session_start_trashes_only_linked_ephemeral_bundles(primary: Path) -> None:
    rule_bundles(primary)
    out = cleanup(primary)
    (folder,) = trash_folders(primary)
    assert f"cleared docs/work/eng-150-handoff: trashed {folder.name}" in out
    assert out[0].endswith("docs 1")
    assert not (primary / "docs/work/eng-150-handoff").exists()
    for stays in ("docs/work/eng-151-research", "docs/work/scratch", "docs/work/eng-3-handoff",
                  "docs/handoffs/eng-152-a"):
        assert (primary / stays / "a.md").exists()
    assert not any("would trash" in line for line in out)


@pytest.mark.parametrize("mode", ["report", "keep", "none"])
def test_report_keep_and_no_strategy_move_no_bundle(primary: Path, mode: str) -> None:
    rule_bundles(primary, "report" if mode == "none" else mode, strategy=mode != "none")
    out = cleanup(primary)
    assert (primary / "docs/work/eng-150-handoff/a.md").exists() and not trash_folders(primary)
    assert out[0].endswith("docs 0")
    assert not any(line.startswith("cleared") for line in out)
    assert any("would trash" in line for line in out) is (mode == "report")


def test_bundles_past_the_time_budget_wait_for_the_next_session_start(
    primary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_bundles(primary)
    with monkeypatch.context() as short:
        short.setattr(sweep_worktrees, "DOC_SECONDS", -1)
        assert sweep_worktrees.cleanup_session(primary)[0].endswith("docs 0")
    assert (primary / "docs/work/eng-150-handoff/a.md").exists()
    assert sweep_worktrees.cleanup_session(primary)[0].endswith("docs 1")
    assert not (primary / "docs/work/eng-150-handoff").exists()


def test_markdown_walk_stops_at_the_deadline_and_moves_nothing(primary: Path) -> None:
    rule_bundles(primary, scope="markdown")
    write(primary, "docs/work/eng-150-handoff/run/out.log", "log")
    cfg = sweep_worktrees.load_config(primary)
    docs = {d["path"]: d for d in sweep_worktrees.build_report(primary, cfg)["docs"]}
    bundle = "docs/work/eng-150-handoff"
    assert sweep_worktrees.clear_bundle(primary, cfg, docs, bundle, "", "stamp", deadline=0) == (False, "")
    assert (primary / bundle / "a.md").exists() and not trash_folders(primary)
    assert sweep_worktrees.clear_bundle(primary, cfg, docs, bundle, "", "stamp")[0]
    assert not (primary / bundle / "a.md").exists() and (primary / bundle / "run/out.log").exists()


def test_session_start_skips_a_bundle_with_no_markdown_left_silently(primary: Path) -> None:
    rule_bundles(primary, scope="markdown")
    write(primary, "docs/work/eng-150-handoff/run/out.log", "log")
    assert cleanup(primary)[0].endswith("docs 1")
    again = cleanup(primary)
    assert again[0].endswith("blocked 2, unverified 3, orphans 0, docs 0")  # the two worktrees only
    assert not any("no Markdown in scope" in line for line in again)
    assert clear(primary, "docs/work/eng-150-handoff") == (
        1, "blocked docs/work/eng-150-handoff: no Markdown in scope"
    )


@pytest.mark.parametrize(
    "error", [OSError(13, "Permission denied", "/abs/secret/path"), RuntimeError("Symlink loop")]
)
def test_a_bundle_that_cannot_be_read_is_a_blocked_line_not_an_error(
    primary: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    rule_bundles(primary)

    def broken(*_: object, **__: object) -> tuple[bool, str]:
        raise error

    monkeypatch.setattr(sweep_worktrees, "clear_bundle", broken)
    out = sweep_worktrees.cleanup_session(primary)
    line = next(l for l in out if l.startswith("blocked docs/work/eng-150-handoff: "))
    assert "/abs" not in line and str(primary) not in line
    assert out[0].endswith("blocked 3, unverified 3, orphans 0, docs 0")  # two worktrees plus this one


STATE = "lumberjack-docs.json"
BUNDLE = "docs/work/eng-160-handoff"


def seen_on(primary: Path, days_ago: int, path: str = BUNDLE) -> None:
    day = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()
    write(primary / ".git", STATE, json.dumps({path: day}))


def recorded(primary: Path) -> dict[str, str]:
    return json.loads((primary / ".git" / STATE).read_text())  # type: ignore[no-any-return]


def grace_bundle(primary: Path, grace_days: int, mode: str = "prune") -> None:
    strategy_settings(primary, grace_days=grace_days)
    (primary / ".lumberjack.toml").write_text(
        (primary / ".lumberjack.toml").read_text() + f'cleanup = "{mode}"\n'
    )
    write(primary, f"{BUNDLE}/a.md", "x\n")


def test_a_linked_bundle_waits_out_its_grace_days_from_the_first_session_start(primary: Path) -> None:
    grace_bundle(primary, 3)
    today = datetime.now(timezone.utc).date().isoformat()
    out = cleanup(primary)
    assert out[0].endswith("docs 0") and any(l.startswith(f"doc ephemeral {BUNDLE}:") for l in out)
    assert recorded(primary) == {BUNDLE: today} and (primary / BUNDLE).exists()
    seen_on(primary, 2)
    assert cleanup(primary)[0].endswith("docs 0") and (primary / BUNDLE).exists()
    seen_on(primary, 3)
    out = cleanup(primary)
    assert out[0].endswith("docs 1") and not (primary / BUNDLE).exists()
    assert any(l.startswith(f"cleared {BUNDLE}: trashed ") for l in out)
    assert recorded(primary) == {}


def test_zero_grace_days_trashes_at_the_first_session_start_that_sees_the_bundle(primary: Path) -> None:
    grace_bundle(primary, 0)
    assert cleanup(primary)[0].endswith("docs 1") and not (primary / BUNDLE).exists()


def test_a_bundle_that_turns_live_or_vanishes_loses_its_date(primary: Path) -> None:
    grace_bundle(primary, 3)
    write(primary, "docs/work/eng-3-handoff/a.md", "x\n")  # the open eng-3 worktree carries it
    write(primary / ".git", STATE, json.dumps({
        "docs/work/eng-3-handoff": "2020-01-01", "docs/work/gone": "2020-01-01",
    }))
    cleanup(primary)
    assert list(recorded(primary)) == [BUNDLE]  # live and gone entries dropped
    assert (primary / "docs/work/eng-3-handoff/a.md").exists()
    # Linked again later: the count starts over instead of trashing at once.
    write(primary / ".git", STATE, json.dumps({BUNDLE: "2020-01-01"}))
    (primary / BUNDLE).rename(primary / "docs/work/elsewhere")  # no ticket: unlinked
    cleanup(primary)
    assert recorded(primary) == {}
    (primary / "docs/work/elsewhere").rename(primary / BUNDLE)
    assert cleanup(primary)[0].endswith("docs 0") and (primary / BUNDLE).exists()


@pytest.mark.parametrize(
    "content",
    ["", "not json", "[]", '{"docs/work/eng-160-handoff": 5}', '{"docs/work/eng-160-handoff": "soon"}',
     b"\xff\xfe"],
)
def test_a_damaged_state_file_never_trashes_early_and_is_rewritten(
    primary: Path, content: str | bytes
) -> None:
    grace_bundle(primary, 3)
    state = primary / ".git" / STATE
    state.write_bytes(content if isinstance(content, bytes) else content.encode())
    out = cleanup(primary)
    assert out[0].endswith("docs 0") and (primary / BUNDLE / "a.md").exists()
    assert recorded(primary) == {BUNDLE: datetime.now(timezone.utc).date().isoformat()}


def test_an_unwritable_state_file_never_trashes_early_or_fails_the_session_start(primary: Path) -> None:
    grace_bundle(primary, 3)
    (primary / ".git" / STATE).mkdir()  # reads and writes both fail
    out = cleanup(primary)
    assert out[0].endswith("docs 0") and (primary / BUNDLE / "a.md").exists()


def test_report_mode_lists_a_bundle_to_trash_only_once_its_grace_is_over(primary: Path) -> None:
    grace_bundle(primary, 3, "report")
    assert not any("would trash" in l for l in cleanup(primary))
    seen_on(primary, 3)
    assert f"doc ephemeral {BUNDLE}: would trash" in cleanup(primary)


def test_clear_ignores_the_grace_period(primary: Path) -> None:
    grace_bundle(primary, 30)
    assert clear(primary, BUNDLE)[0] == 0 and not (primary / BUNDLE).exists()


def test_trashing_one_bundle_keeps_the_date_of_one_still_waiting(primary: Path) -> None:
    grace_bundle(primary, 3)
    write(primary, "docs/work/eng-161-handoff/a.md", "x\n")
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    write(primary / ".git", STATE, json.dumps({BUNDLE: "2020-01-01", "docs/work/eng-161-handoff": yesterday}))
    out = cleanup(primary)
    assert out[0].endswith("docs 1") and not (primary / BUNDLE).exists()
    assert recorded(primary) == {"docs/work/eng-161-handoff": yesterday}
