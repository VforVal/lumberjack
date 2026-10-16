# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Inventory worktrees, orphan directories, branches and the primary checkout."""

import argparse
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

Row = dict[str, Any]
Config = dict[str, Any]
# Neutral defaults: any owner, no ticket prefix, nothing extra protected from prune.
# .lumberjack.toml, else [tool.lumberjack] in pyproject.toml, overrides them.
DEFAULTS: Config = {
    "owners": [],
    "ticket_prefix": "",
    "prototype_prefix": "prototype/",
    "primary_branch": "main",
    "evidence_dir": "",
    "worktree_roots": [
        "~/.codex/worktrees/*",
        ".claude/worktrees",
        "~/worktrees/{name}",
    ],
    "cleanup": "prune",
    "stop_gate": True,
}
# .codex-worktree-name is all Codex leaves behind after removing a worktree.
# onexc replaced the deprecated onerror in 3.12; the handler signature is the same.
RMTREE_HANDLER = "onexc" if sys.version_info >= (3, 12) else "onerror"
CLEANUP_MODES = ("prune", "report", "keep")
CACHE_DIRS = {"node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist"}
CACHE_DIRS.add(".codex-worktree-name")


def branch_pattern(cfg: Config) -> re.Pattern[str]:
    owners = "|".join(map(re.escape, cfg["owners"])) or r"[a-z0-9][a-z0-9_-]*"
    ticket = rf"{re.escape(cfg['ticket_prefix'])}\d+-" if cfg["ticket_prefix"] else ""
    slug = r"[a-z0-9][a-z0-9_-]*"
    proto = re.escape(cfg["prototype_prefix"])
    return re.compile(rf"^({owners})/({ticket})?{slug}$|^{proto}{ticket}{slug}$")


class ConfigError(Exception):
    """A bad lumberjack config; the Stop gate blocks on it."""

    def __init__(self, message: str, source: str = "") -> None:
        super().__init__(message)
        self.source = source  # the config file name, when one was found


CONFIG_FILES = ((".lumberjack.toml", ()), ("pyproject.toml", ("tool", "lumberjack")))


def config_table(primary: Path) -> tuple[str, Config]:
    """(file name, raw table) from the first config file found; ("", {}) when none."""
    # shortcut: first file found wins, no merge across files; revisit if both are needed.
    for name, section in CONFIG_FILES:
        path = primary / name
        if not path.is_file():
            continue
        try:
            with path.open("rb") as file:
                data = tomllib.load(file)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"lumberjack: bad TOML in {path}: {exc}", name) from exc
        for key in section:
            data = data.get(key, {})
        if not section or data:
            return name, data
    return "", {}


def load_config(primary: Path) -> Config:
    source, table = config_table(primary)
    if unknown := sorted(table.keys() - DEFAULTS.keys()):
        raise ConfigError("lumberjack: unknown key: " + ", ".join(unknown), source)
    cfg = {**DEFAULTS, **table}
    for key, default in DEFAULTS.items():
        if not isinstance(cfg[key], type(default)):
            raise ConfigError(f"lumberjack: {key} must be {type(default).__name__}", source)
    if not cfg["prototype_prefix"]:
        raise ConfigError("lumberjack: prototype_prefix must not be empty", source)
    if cfg["cleanup"] not in CLEANUP_MODES:
        raise ConfigError(
            f"lumberjack: cleanup must be one of {', '.join(CLEANUP_MODES)}", source
        )
    return cfg


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # A git hook exports GIT_DIR and friends; they would point every call at the merging worktree.
    leaked = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    env = {key: value for key, value in os.environ.items() if key not in leaked}
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env
    )


def lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stdout.splitlines() if result.returncode == 0 else []


def find_primary(start: Path) -> Path:
    result = git(start, "rev-parse", "--git-common-dir")
    if result.returncode:
        raise SystemExit(f"sweep: not a git repository: {start}")
    return (start / result.stdout.strip()).resolve().parent


def registered(primary: Path) -> list[Row]:
    """Registered worktrees, primary first."""
    rows: list[Row] = []
    for line in lines(git(primary, "worktree", "list", "--porcelain")):
        key, _, value = line.partition(" ")
        if key == "worktree":
            rows.append({"path": value, "head": "", "branch": ""})
        elif key == "HEAD":
            rows[-1]["head"] = value
        elif key == "branch":
            rows[-1]["branch"] = value.removeprefix("refs/heads/")
        elif key == "prunable":
            rows[-1]["gone"] = "yes"
    # A worktree whose directory is gone has nothing to sweep; git worktree prune would drop it.
    return [row for row in rows if "gone" not in row]


# Verified tiers are safe to prune; landed-probable and unverified are report-only.
VERIFIED = {"integrated", "content-integrated", "landed-pr"}
GH_TIMEOUT = 5
GH_BATCH = 100
FETCH_TIMEOUT = 5
Tip = tuple[str, str]  # (commit sha, branch name; empty when detached)
Verdict = tuple[str, str]  # (tier, reason; the reason is empty unless unverified)


def origin_parts(url: str) -> tuple[str, str, str]:
    """(host, owner, name) of an https, ssh or scp-style remote URL; empty when absent."""
    host = re.match(r"(?:[a-z+]+://)?(?:[^@/:]+@)?([^/:]+)[/:]", url)
    tail = re.search(r"[/:]([^/:]+)/([^/:]+?)(?:\.git)?/?$", url)
    owner, name = tail.groups() if tail else ("", "")
    return (host.group(1) if host else ""), owner, name


def local_tier(primary: Path, base: str, tree: str, sha: str) -> str:
    if git(primary, "merge-base", "--is-ancestor", sha, base).returncode == 0:
        return "integrated"
    merged = git(primary, "merge-tree", "--write-tree", "--no-messages", base, sha)
    if merged.returncode == 0 and merged.stdout.splitlines()[:1] == [tree]:
        return "content-integrated"
    return ""  # a conflict or error is inconclusive


def merged_prs(primary: Path, branches: list[str]) -> dict[str, list[Row]] | str:
    """Merged PRs per branch from one GraphQL call, or the reason it failed."""
    gh = shutil.which("gh")
    if not gh:
        return "gh missing"
    host, owner, repo = origin_parts(git(primary, "remote", "get-url", "origin").stdout.strip())
    if host != "github.com" or not (owner and repo):
        return "not a github remote"
    owner, repo = json.dumps(owner), json.dumps(repo)
    # shortcut: branches past GH_BATCH are not queried; batch the rest if that bites.
    aliases = " ".join(
        f"b{i}: pullRequests(headRefName: {json.dumps(name)}, states: MERGED, first: 10)"
        " { nodes { headRefOid baseRefName mergeCommit { oid } } }"
        for i, name in enumerate(branches[:GH_BATCH])
    )
    query = f"query {{ repository(owner: {owner}, name: {repo}) {{ {aliases} }} }}"
    try:
        done = subprocess.run(
            [gh, "api", "graphql", "-f", f"query={query}"],
            cwd=primary,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=GH_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "unreachable"
    if done.returncode == 4 or "auth" in done.stderr.lower():
        return "unauthenticated"
    try:
        if done.returncode:
            return "unreachable"
        found = json.loads(done.stdout)["data"]["repository"]
        return {name: found[f"b{i}"]["nodes"] for i, name in enumerate(branches[:GH_BATCH])}
    except (ValueError, KeyError, TypeError):
        return "unreachable"


def pr_verdict(prs: dict[str, list[Row]] | str, tip: Tip, primary_branch: str) -> Verdict:
    """(tier, reason) from the merged-PR lookup; tier is empty on a miss."""
    if isinstance(prs, str):
        return "", prs
    nodes = prs.get(tip[1], [])
    into = [n for n in nodes if n["baseRefName"] == primary_branch]
    if any(n["headRefOid"] == tip[0] for n in into):
        return "landed-pr", ""
    if into:
        return "", "tip diverged"
    return "", "non-primary base" if nodes else ""


def patch_ids(primary: Path, *args: str) -> list[str]:
    shown = subprocess.run(["git", *args], cwd=primary, capture_output=True)
    ids = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=primary,
        input=shown.stdout,
        capture_output=True,
    )
    return [line.split()[0] for line in ids.stdout.decode().splitlines()]


def probable(primary: Path, base: str, sha: str, recent: set[str]) -> bool:
    cherry = lines(git(primary, "cherry", base, sha))
    if cherry and all(line.startswith("-") for line in cherry):
        return True
    return bool(set(patch_ids(primary, "diff", f"{base}...{sha}")) & recent)


def classify(primary: Path, cfg: Config, tips: set[Tip], stale: bool = False) -> dict[Tip, Verdict]:
    """(tier, reason) per tip against origin/<primary>, first decisive tier wins."""
    base = f"origin/{cfg['primary_branch']}"
    tree = git(primary, "rev-parse", f"{base}^{{tree}}").stdout.strip()
    result: dict[Tip, Verdict] = {}
    rest = []
    for tip in sorted(tips):
        if tier := local_tier(primary, base, tree, tip[0]):
            result[tip] = (tier, "")
        else:
            rest.append(tip)
    if not rest:
        return result
    names = sorted({name for _, name in rest if name})
    prs = merged_prs(primary, names) if names else {}  # a detached tip has no pull request
    recent = set(patch_ids(primary, "log", "-200", "-p", base))
    for tip in rest:
        tier, reason = pr_verdict(prs, tip, cfg["primary_branch"])
        if not tier:
            tier = "landed-probable" if probable(primary, base, tip[0], recent) else ""
        if not tier:
            tier = "unverified"
            reason = (reason or "no pull request") + ("; origin stale" if stale else "")
        result[tip] = (tier, reason)
    return result


def worktree_rows(trees: list[Row], tiers: dict[Tip, Verdict]) -> list[Row]:
    result = []
    for tree in trees[1:]:
        path = Path(tree["path"])
        status = git(path, "status", "--porcelain")
        tier, reason = tiers[(tree["head"], tree["branch"])]
        result.append(
            {
                "state": "integrated" if tier in VERIFIED else "open",
                "tier": tier,
                "reason": reason,
                # -1 means status failed; prune treats it as dirty.
                "dirty": len(lines(status)) if status.returncode == 0 else -1,
                "head": tree["head"],
                "node_modules": "yes" if (path / "node_modules").is_dir() else "no",
                "branch": tree["branch"],
                "path": tree["path"],
            }
        )
    return result


def orphan_candidates(primary: Path, cfg: Config) -> list[Path]:
    found: list[Path] = []
    for root in cfg["worktree_roots"]:
        # shortcut: glob metacharacters in the primary or home path are not escaped.
        path = os.path.expanduser(root.replace("{name}", primary.name))
        pattern = os.path.abspath(os.path.join(primary, path))  # an absolute path wins the join
        starred = "*" in root
        # Outside the repo and without {name}, the root holds other repos' directories too.
        inside = Path(pattern).is_relative_to(os.path.abspath(primary))
        shared = "{name}" not in root and not inside
        for d in map(Path, glob.glob(pattern if starred else f"{pattern}/*")):
            # A starred match may hold the checkout in a <name> subdirectory (Codex).
            nested = d / primary.name
            if (starred or shared) and nested.is_dir():
                found.append(nested)
            elif not shared:
                found.append(d)
    return [path for path in found if path.is_dir()]


def classify_orphan(path: Path) -> str:
    entries = list(path.iterdir())
    if entries and all(entry.name in CACHE_DIRS for entry in entries):
        return "node_modules-only"
    return "mixed" if (path / ".git").exists() else "not-git"


def same_dir(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(Path(a).resolve()) == os.path.normcase(Path(b).resolve())


def orphan_rows(primary: Path, cfg: Config, trees: list[Row]) -> list[Row]:
    return [
        {"kind": classify_orphan(path), "path": path.as_posix()}
        for path in sorted(orphan_candidates(primary, cfg))
        if not any(same_dir(path, tree["path"]) for tree in trees)
    ]


def branch_tips(primary: Path, cfg: Config, trees: list[Row]) -> dict[str, str]:
    """Unmerged local branches that no worktree has checked out, with their tips."""
    checked_out = {tree["branch"] for tree in trees}
    base = f"origin/{cfg['primary_branch']}"
    names = lines(
        git(primary, "branch", "--format=%(refname:short)", "--no-merged", base)
    )
    return {
        name: git(primary, "rev-parse", f"refs/heads/{name}").stdout.strip()
        for name in names
        if name not in checked_out
    }


def branch_rows(tips: dict[str, str], tiers: dict[Tip, Verdict]) -> list[Row]:
    rows = []
    for name, sha in tips.items():
        tier, reason = tiers[(sha, name)]
        state = "integrated" if tier in VERIFIED else "open"
        rows.append({"head": sha, "state": state, "tier": tier, "reason": reason, "name": name})
    return rows


def prototype_rows(primary: Path, cfg: Config) -> list[Row]:
    prefix = cfg["prototype_prefix"].rstrip("/")
    refs = lines(
        git(
            primary,
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/heads/{prefix}",
            f"refs/remotes/origin/{prefix}",
        )
    )
    names = sorted({ref.removeprefix("origin/") for ref in refs})
    ticket = cfg["ticket_prefix"]
    return [
        {
            "issue": f"{ticket}{m[1]}"
            if ticket and (m := re.search(rf"{re.escape(ticket)}(\d+)", n))
            else "none",
            "name": n,
        }
        for n in names
    ]


def primary_row(primary: Path, cfg: Config) -> Row:
    branch = git(primary, "branch", "--show-current").stdout.strip()
    dirty = len(lines(git(primary, "status", "--porcelain")))
    warning = branch != cfg["primary_branch"] or dirty > 0
    return {"branch": branch, "dirty": dirty, "warning": warning}


def detail(row: Row) -> str:
    return f" tier={row['tier']}" + (f" reason={row['reason']!r}" if row["reason"] else "")


def format_rows(report: Row) -> list[str]:
    out = [
        f"worktree {w['state']}{detail(w)} dirty={w['dirty']} node_modules={w['node_modules']}"
        f" {w['branch'] or '(detached)'} {w['path']}"
        for w in report["worktrees"]
    ]
    out += [f"orphan {o['kind']} {o['path']}" for o in report["orphans"]]
    out += [f"branch {b['state']}{detail(b)} {b['name']}" for b in report["branches"]]
    out += [f"prototype {p['issue']} {p['name']}" for p in report["prototypes"]]
    p = report["primary"]
    out.append(
        f"primary {p['branch'] or '(detached)'} dirty={p['dirty']}"
        + (" WARNING" if p["warning"] else "")
    )
    out += [f"name violation {name}" for name in report["name_violations"]]
    return out


def build_report(primary: Path, cfg: Config, stale: bool = False) -> Row:
    trees = registered(primary)
    loose = branch_tips(primary, cfg, trees)
    tips = {(t["head"], t["branch"]) for t in trees[1:]}
    tips |= {(sha, name) for name, sha in loose.items()}
    tiers = classify(primary, cfg, tips, stale)
    local = lines(git(primary, "branch", "--format=%(refname:short)"))
    pattern = branch_pattern(cfg)
    return {
        "worktrees": worktree_rows(trees, tiers),
        "orphans": orphan_rows(primary, cfg, trees),
        "branches": branch_rows(loose, tiers),
        "prototypes": prototype_rows(primary, cfg),
        "primary": primary_row(primary, cfg),
        "name_violations": [
            b for b in local if b != cfg["primary_branch"] and not pattern.match(b)
        ],
    }


def make_writable(func: Callable[[str], object], path: str, _: object) -> None:
    os.chmod(path, stat.S_IWRITE)  # Windows refuses to delete read-only files.
    func(path)


def fetch(primary: Path) -> bool:
    """True when origin may be stale: the fetch failed or exceeded FETCH_TIMEOUT."""
    try:
        done = subprocess.run(
            ["git", "fetch", "-q", "--prune", "origin"],
            cwd=primary,
            capture_output=True,
            timeout=FETCH_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True
    return done.returncode != 0


def unpushed(path: Path) -> bool:
    """No upstream, or the local tip is not contained in the remote-tracking ref."""
    if git(path, "rev-parse", "--verify", "-q", "@{u}").returncode:
        return True
    return git(path, "merge-base", "--is-ancestor", "HEAD", "@{u}").returncode != 0


def short_path(primary: Path, path: str | Path) -> str:
    """Path relative to the primary's parent directory, else with home as ~."""
    full = Path(path).resolve()
    for base, prefix in ((primary.parent.resolve(), ""), (Path.home().resolve(), "~/")):
        if full.is_relative_to(base):
            return prefix + full.relative_to(base).as_posix()
    return full.as_posix()


def block_reason(cfg: Config, w: Row, here: Path | None, pushed: bool = True) -> str:
    """Why a worktree stays, or an empty string; pushed=False skips the upstream check."""
    evidence = cfg["evidence_dir"] and Path(w["path"]) / cfg["evidence_dir"]
    if here is not None and same_dir(w["path"], here):
        return "current checkout"  # never pull the directory from under the running session
    if w["dirty"]:  # -1 (status failed) counts as dirty
        return "dirty"
    # landed-pr: the forge saw this exact tip, and delete-on-merge drops the upstream.
    if pushed and w["tier"] != "landed-pr" and unpushed(Path(w["path"])):
        return "unpushed"
    if evidence and evidence.is_dir() and any(evidence.iterdir()):
        return "evidence"
    return ""


def tag(primary: Path, w: Row) -> str:
    return f"{w['branch'] or '(detached)'} {short_path(primary, w['path'])}"


def worktree_event(primary: Path, cfg: Config, w: Row, here: Path | None) -> tuple[str, str]:
    """(count key or empty, output line) for a verified-landed worktree outside keep mode."""
    if reason := block_reason(cfg, w, here):
        return "blocked", f"blocked {tag(primary, w)}: {reason}"
    if cfg["cleanup"] == "report":
        return "", f"landed {tag(primary, w)}: would remove"
    return remove_worktree(primary, w)


def remove_worktree(primary: Path, w: Row) -> tuple[str, str]:
    if (gone := git(primary, "worktree", "remove", w["path"])).returncode:
        return "blocked", f"blocked {tag(primary, w)}: {gone.stderr.strip()}"
    return "cleaned", f"cleaned {tag(primary, w)} (local branch kept)"


def remove_named(start: Path, target: str) -> tuple[bool, str]:
    """Remove the one worktree at target, whatever its tier; (removed, output line)."""
    primary = find_primary(start)
    cfg = load_config(primary)
    trees = registered(primary)
    tree = next((t for t in trees if same_dir(t["path"], target)), None)
    if tree is None:
        return False, f"blocked {target}: not a worktree"
    status = git(Path(tree["path"]), "status", "--porcelain")
    w = {**tree, "tier": "", "dirty": len(lines(status)) if status.returncode == 0 else -1}
    here = Path(git(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    on_branch = ("for-each-ref", "--contains", w["head"], "--count=1", "refs/heads")
    if tree is trees[0]:
        reason = "primary checkout"
    elif tree["branch"].startswith(cfg["prototype_prefix"]):
        reason = "prototype"
    elif not tree["branch"] and not lines(git(primary, *on_branch)):
        reason = "no branch"  # removal would orphan the commits of a detached tip
    else:
        # The kept branch holds the commits, so unpushed is no loss; ignored files are.
        reason = block_reason(cfg, w, here, pushed=False)
        reason = reason or ("ignored files" if stray_ignored(Path(tree["path"])) else "")
    if reason:
        return False, f"blocked {tag(primary, w)}: {reason}"
    outcome, line = remove_worktree(primary, w)
    return outcome == "cleaned", line


def stray_ignored(path: Path) -> bool:
    """Ignored entries other than caches; git worktree remove deletes them silently."""
    ignored = lines(git(path, "status", "--porcelain", "--ignored"))
    names = [Path(line[3:].rstrip("/")).name for line in ignored if line.startswith("!! ")]
    return any(name not in CACHE_DIRS for name in names)


def dependent_hints(primary: Path, trunk: str, name: str, sha: str) -> list[str]:
    """One rebase hint per branch built on a landed tip that is not yet in trunk's history."""
    contains = git(primary, "branch", "--contains", sha, "--format=%(refname:short)")
    return [
        f"dependent {d} of {name}: git rebase --onto {trunk} {sha[:12]} {d}"
        for d in lines(contains)
        if d not in (name, trunk)
    ]


def clean_orphans(primary: Path, orphans: list[Row], mode: str) -> tuple[int, list[str]]:
    """Delete cache-only orphan directories in prune mode; (count deleted, lines)."""
    cleaned, out = 0, []
    for o in orphans:
        shown = short_path(primary, o["path"])
        if o["kind"] == "node_modules-only" and mode == "prune":
            try:
                shutil.rmtree(o["path"], **{RMTREE_HANDLER: make_writable})
            except OSError as error:
                out.append(f"orphan {o['kind']} {shown}: {error}")
            else:
                cleaned += 1
                out.append(f"cleaned orphan {shown}")
        elif mode != "keep":
            out.append(f"orphan {o['kind']} {shown}")
    return cleaned, out


def run_cleanup(
    primary: Path, cfg: Config, report: Row, stale: bool, here: Path | None = None
) -> list[str]:
    """Remove verified-landed, clean, pushed worktrees (the branch stays); one line per event."""
    mode, trunk = cfg["cleanup"], cfg["primary_branch"]
    out: list[str] = []
    counts = dict.fromkeys(("cleaned", "landed", "dependents", "blocked", "unverified"), 0)
    for row in (*report["worktrees"], *report["branches"]):
        name, is_worktree = row.get("branch", row.get("name", "")), "path" in row
        if name.startswith(cfg["prototype_prefix"]):
            continue
        if row["tier"] == "unverified":
            diverged = is_worktree and row["reason"].startswith("tip diverged") and mode != "keep"
            counts["blocked" if diverged else "unverified"] += 1
            if diverged:
                out.append(f"blocked {tag(primary, row)}: tip diverged")
            continue
        if row["tier"] not in VERIFIED:  # landed-probable: report only
            continue
        counts["landed"] += 1
        # An integrated tip is already in trunk's history: nothing to rebase.
        if mode != "keep" and row["tier"] != "integrated":
            hints = dependent_hints(primary, trunk, name, row["head"])
            counts["dependents"] += len(hints)
            out += hints
        if is_worktree and mode != "keep":
            key, line = worktree_event(primary, cfg, row, here)
            out.append(line)
            if key:
                counts[key] += 1
    cleaned, orphan_lines = clean_orphans(primary, report["orphans"], mode)
    counts["cleaned"] += cleaned
    out += orphan_lines
    summary = (
        f"sweep: cleaned {counts['cleaned']}, landed {counts['landed']},"
        f" dependents {counts['dependents']}, blocked {counts['blocked']},"
        f" unverified {counts['unverified']}, orphans {len(report['orphans'])}"
        + (", origin stale" if stale else "")
    )
    return [summary, *out]


def hook_manager(primary: Path) -> str:
    """The git hook manager the repository already uses, or an empty string."""
    has = primary.joinpath
    for name, found in (
        ("husky", has(".husky").is_dir()),
        ("lefthook", has("lefthook.yml").is_file() or has(".lefthook.yml").is_file()),
        ("pre-commit", has(".pre-commit-config.yaml").is_file()),
    ):
        if found:
            return name
    try:
        data = json.loads(has("package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return "simple-git-hooks" if isinstance(data, dict) and "simple-git-hooks" in data else ""


# Every hook setup writes for a manager runs the script named by lumberjack.script.
MANAGER_FILES = (
    ".husky/post-merge",
    "lefthook.yml",
    ".lefthook.yml",
    "lefthook-local.yml",
    ".pre-commit-config.yaml",
    "package.json",
)


def manager_wired(primary: Path) -> bool:
    for rel in MANAGER_FILES:
        try:
            if "lumberjack.script" in (primary / rel).read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def hook_script(primary: Path) -> str:
    """Keep this repository's hook outside its removable source worktrees."""
    script = Path(__file__).resolve()
    stable = primary / "scripts" / script.name
    if stable.is_file() and any(
        script == Path(tree["path"]).resolve() / "scripts" / script.name
        for tree in registered(primary)
    ):
        script = stable
    return script.as_posix()


def self_heal(primary: Path) -> list[str]:
    """Point git at this script and, when it is free, at .githooks; hint on unwired managers."""
    git(primary, "config", "lumberjack.script", hook_script(primary))
    if name := hook_manager(primary):
        if manager_wired(primary):
            return []
        return [f"hint: post-merge hook managed by {name}; run /lumberjack:setup to add lumberjack"]
    hooks_path = git(primary, "config", "core.hooksPath").stdout.strip()
    hook = primary / ".githooks" / "post-merge"
    # Only our own hook (marked by setup) may claim core.hooksPath.
    if hook.is_file() and "# >>> lumberjack" in hook.read_text(encoding="utf-8", errors="replace") and not hooks_path:
        git(primary, "config", "core.hooksPath", ".githooks")
    return []


def cleanup_session(start: Path, include_context: bool = False) -> list[str]:
    """SessionStart mode: self-heal, then the cleanup rule. Never raises, never prompts."""
    try:
        primary = find_primary(start)
    except SystemExit as error:
        return [str(error)]
    here = Path(git(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    # git exports GIT_DIR to hooks: a merge inside a linked worktree must not clean up.
    if "GIT_DIR" in os.environ and not same_dir(here, primary):
        return ["sweep: not the primary checkout; skipped"]
    notes = self_heal(primary)
    try:
        cfg = load_config(primary)
    except ConfigError as error:
        return [str(error), *notes]
    if include_context:
        notes.insert(0, active_settings(cfg))
    trunk = cfg["primary_branch"]
    if git(primary, "branch", "--show-current").stdout.strip() != trunk:
        return [f"sweep: primary checkout is not on {trunk}; skipped", *notes]
    stale = fetch(primary)
    if git(primary, "rev-parse", "--verify", "-q", f"origin/{trunk}").returncode:
        return [f"sweep: origin/{trunk} is missing; skipped", *notes]
    try:
        report = build_report(primary, cfg, stale)
        return [*run_cleanup(primary, cfg, report, stale, here), *notes]
    except OSError as error:
        return [f"sweep: unavailable ({error})", *notes]


def gate(start: Path) -> list[str]:
    """Debt in the checkout a session runs in; the Stop hook reports it once."""
    primary = find_primary(start)
    cfg = load_config(primary)
    if not cfg["stop_gate"]:
        return []
    branch = git(start, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    here = Path(git(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    reasons = []
    ok = {cfg["primary_branch"], "HEAD"}
    if branch not in ok and not branch_pattern(cfg).match(branch):
        ticket = f"{cfg['ticket_prefix']}<n>-" if cfg["ticket_prefix"] else ""
        reasons.append(
            f"gate: branch {branch} violates the lumberjack skill naming;"
            f" rename it: git branch -m <owner>/{ticket}<slug>"
        )
    if here == primary and branch != cfg["primary_branch"]:
        reasons.append(
            f"gate: the primary checkout is on {branch}; move the work to a"
            f" worktree and return the primary to {cfg['primary_branch']}"
        )
    return reasons


def gate_reasons(start: Path) -> list[str]:
    try:
        return gate(start)
    except ConfigError as error:
        return [str(error)]
    except (OSError, SystemExit):  # not a git repository: never block
        return []


def hook_input() -> dict[str, object]:
    try:
        payload = json.loads("{}" if sys.stdin.isatty() else sys.stdin.read() or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def active_settings(cfg: Config) -> str:
    return "Active lumberjack settings (override the defaults below):\n" + json.dumps(cfg)


def hook(payload: dict[str, object]) -> int:
    """The one harness entry point: dispatch on the event the hook input names."""
    event = payload.get("hook_event_name")
    if event == "Stop":
        reasons = [] if payload.get("stop_hook_active") else gate_reasons(Path.cwd())
        if reasons:
            print(json.dumps({"decision": "block", "reason": "\n".join(reasons)}))
        return 0
    if event == "SessionStart":
        output = "\n".join(cleanup_session(Path.cwd(), include_context=True))
    elif event == "SubagentStart":
        # Claude forks inherit context; Codex children always receive the approved refresh.
        if "turn_id" not in payload and payload.get("agent_type") == "fork":
            return 0
        try:
            output = active_settings(load_config(find_primary(Path.cwd())))
        except (ConfigError, SystemExit) as error:
            output = str(error)
    else:
        print(
            "lumberjack: --hook requires SessionStart, SubagentStart or Stop hook input",
            file=sys.stderr,
        )
        return 1
    skill = Path(__file__).resolve().parents[1] / "skills" / "lumberjack" / "SKILL.md"
    rules = skill.read_text(encoding="utf-8").split("---", 2)[-1].strip()
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": output + "\n\n" + rules,
    }}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--fetch", action="store_true", help="git fetch origin first")
    parser.add_argument(
        "--hook", action="store_true",
        help="harness hook: read the hook input on stdin and act on its hook_event_name",
    )
    parser.add_argument("--cleanup", action="store_true", help="remove landed worktrees, exit 0")
    parser.add_argument("--gate", action="store_true", help="exit 2 on debt")
    parser.add_argument(
        "--remove", metavar="PATH",
        help="remove only this worktree, any tier, keeping its branch; exit 1 when blocked",
    )
    args = parser.parse_args(argv)
    if args.remove is not None:
        try:
            removed, line = remove_named(Path.cwd(), args.remove)
        except ConfigError as error:
            raise SystemExit(error) from error
        print(line)
        return 0 if removed else 1
    if args.hook:
        return hook(hook_input())
    if args.gate:
        if hook_input().get("stop_hook_active"):
            return 0
        reasons = gate_reasons(Path.cwd())
        if reasons:
            print("\n".join(reasons), file=sys.stderr)
        return 2 if reasons else 0
    if args.cleanup:
        print("\n".join(cleanup_session(Path.cwd())))
        return 0
    primary = find_primary(Path.cwd())
    stale = args.fetch and fetch(primary)
    if stale:
        print("sweep: git fetch origin failed", file=sys.stderr)
    try:
        cfg = load_config(primary)
    except ConfigError as error:
        raise SystemExit(error) from error
    base = f"origin/{cfg['primary_branch']}"
    if git(primary, "rev-parse", "--verify", "-q", base).returncode:
        raise SystemExit(f"sweep: {base} is missing; run git fetch origin")
    report = build_report(primary, cfg, stale)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n".join(format_rows(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
