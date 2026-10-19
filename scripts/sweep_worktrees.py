"""Inventory worktrees, orphan directories, branches and the primary checkout."""

import argparse
import glob
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import time
from datetime import date, datetime, timezone

if sys.version_info < (3, 11):  # before tomllib: a plain message instead of a traceback
    sys.exit(f"lumberjack: Python 3.11+ required, found {sys.version.split()[0]}")
import tomllib
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
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
    "doc_roots": [],
    "doc_reference": {},
    "doc_strategy": {},
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
DOC_REFERENCE = ["research", "prototype"]  # bundle name words that make a bundle reference
DOC_COMMITS = 1000  # newest commits read from unlanded branches when looking for bundle names
TRASH_STAMP = "%Y%m%dT%H%M%SZ"  # a trash folder is named for the UTC moment its files moved
SCOPES = ("markdown", "bundle")  # doc_strategy scope: only .md files, or whole bundles
BUNDLE_ACTION = {"ephemeral": "trash", "reference": "promote"}  # by class, outside a keep-root
DOC_SECONDS = 10  # one budget for all doc work in a run (sizing, trash sizing, trashing); the hook times out at 30
NO_MARKDOWN = "no Markdown in scope"


def budget(deadline: float | None) -> float:
    """The run's shared doc deadline, or a fresh one for a caller that has none."""
    return time.monotonic() + DOC_SECONDS if deadline is None else deadline


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
    return parse_config(table, source)


def parse_config(table: Config, source: str = "") -> Config:
    """The validated config for a raw table; source names its file in errors."""
    if unknown := sorted(table.keys() - DEFAULTS.keys()):
        raise ConfigError("lumberjack: unknown key: " + ", ".join(unknown), source)
    cfg = {**DEFAULTS, **table}
    for key, default in DEFAULTS.items():
        if not isinstance(cfg[key], type(default)):
            raise ConfigError(f"lumberjack: {key} must be {type(default).__name__}", source)
    if not cfg["prototype_prefix"]:
        raise ConfigError("lumberjack: prototype_prefix must not be empty", source)
    # git refuses a path outside the worktree, which would read as `docs` and keep every worktree.
    if not all(
        isinstance(root, str) and root and not PureWindowsPath(root).anchor
        and ".." not in PureWindowsPath(root).parts and posixpath.normpath(root) != "."
        for root in cfg["doc_roots"]
    ):
        raise ConfigError("lumberjack: doc_roots must be relative paths inside the repo", source)
    if not all(
        isinstance(words, list) and all(isinstance(w, str) for w in words)
        for words in cfg["doc_reference"].values()
    ):
        raise ConfigError("lumberjack: doc_reference must map each root to a list of words", source)
    strategy = cfg["doc_strategy"]
    roots = strategy.get("roots", {})  # optional: doc roots whose bundles are always kept
    if strategy.keys() & {"ephemeral", "reference", "tracker"}:
        raise ConfigError(
            "lumberjack: doc_strategy no longer takes ephemeral, reference or tracker;"
            " keep scope, trash_days, grace_days and roots", source,
        )
    if strategy and not (
        strategy.keys() - {"roots"} == {"scope", "trash_days", "grace_days"}
        and strategy["scope"] in SCOPES
        and type(strategy["trash_days"]) is int and strategy["trash_days"] > 0
        and type(strategy["grace_days"]) is int and strategy["grace_days"] >= 0
        and isinstance(roots, dict)
        and all(isinstance(r, str) and a == "keep" for r, a in roots.items())
    ):
        raise ConfigError(
            "lumberjack: doc_strategy must be empty or set scope (markdown or bundle) and"
            " trash_days (at least 1) and grace_days (0 or more), optionally roots (each root = \"keep\")", source,
        )
    if roots:
        strategy["roots"] = {posixpath.normpath(r): a for r, a in roots.items()}
    # One spelling per root, so "docs/work/" in doc_reference finds the root "./docs/work".
    cfg["doc_roots"] = [posixpath.normpath(root) for root in cfg["doc_roots"]]
    cfg["doc_reference"] = {posixpath.normpath(k): v for k, v in cfg["doc_reference"].items()}
    if cfg["cleanup"] not in CLEANUP_MODES:
        raise ConfigError(
            f"lumberjack: cleanup must be one of {', '.join(CLEANUP_MODES)}", source
        )
    return cfg


def git(cwd: Path, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
    # A git hook exports GIT_DIR and friends; they would point every call at the merging worktree.
    leaked = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    env = {key: value for key, value in os.environ.items() if key not in leaked}
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, input=input,
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
        elif key == "locked":
            rows[-1]["locked"] = "yes"
    # A worktree whose directory is gone has nothing to sweep; git worktree prune would drop it.
    return [row for row in rows if "gone" not in row]


# Verified tiers are safe to prune; landed-probable and unverified are report-only.
VERIFIED = {"integrated", "content-integrated", "landed-pr"}
GH_TIMEOUT = 5
GH_BATCH = 100
FETCH_TIMEOUT = 5
FRESH_HOURS = 24
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
                "locked": tree.get("locked", ""),
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
    out += map(doc_line, linked_docs(report))
    out += map(trash_line, report["trash"])
    out += [f"branch {b['state']}{detail(b)} {b['name']}" for b in report["branches"]]
    out += [f"prototype {p['issue']} {p['name']}" for p in report["prototypes"]]
    p = report["primary"]
    out.append(
        f"primary {p['branch'] or '(detached)'} dirty={p['dirty']}"
        + (" WARNING" if p["warning"] else "")
    )
    out += [f"name violation {name}" for name in report["name_violations"]]
    return out


def ticket_ids(cfg: Config, text: str) -> list[str]:
    """Ticket ids (`ticket_prefix` + digits) in text, in order; none without a prefix."""
    prefix = cfg["ticket_prefix"]
    return re.findall(rf"(?<![A-Za-z0-9]){re.escape(prefix)}\d+", text) if prefix else []


def untracked_docs(path: Path, roots: list[str]) -> list[str] | None:
    """Untracked or ignored entries under roots, outermost only; None when git fails."""
    # --directory prints one line per untracked folder, so huge doc roots stay cheap.
    found = git(path, "ls-files", "-z", "--others", "--directory", "--no-empty-directory",
                "--", *roots)
    return None if found.returncode else [e.rstrip("/") for e in found.stdout.split("\0") if e]


def doc_entries(primary: Path, cfg: Config) -> list[tuple[str, str]]:
    """(doc root, repo-relative path) of each outermost untracked entry under a doc root."""
    found: dict[str, str] = {}
    for root in cfg["doc_roots"]:
        for entry in untracked_docs(primary, [root]) or []:
            if entry == root:  # the whole root is untracked: its children are the bundles
                try:
                    found |= {f"{root}/{e.name}": root for e in os.scandir(primary / root)}
                except OSError:
                    pass
            else:  # the outermost untracked file or folder, below tracked folders too
                found[entry] = root
    return [(root, path) for path, root in sorted(found.items())]


def top_file_tickets(cfg: Config, path: Path) -> list[str]:
    """Ids on a `Ticket:` or `Tickets:` line in the head of the bundle's top file."""
    try:
        if path.is_dir():  # shortcut: top file = first Markdown file, else first file, by name
            files = [e for e in os.scandir(path) if e.is_file()]
            if not files:
                return []
            path = Path(min(files, key=lambda e: (not e.name.endswith(".md"), e.name.lower())).path)
        with path.open(encoding="utf-8", errors="replace") as file:
            head = file.read(4096)  # the one place file contents are read
    except OSError:
        return []
    return [
        t for line in head.splitlines() if re.match(r"[\W_]*tickets?\W*:", line, re.I)
        for t in ticket_ids(cfg, line)
    ]


def tree_size(path: Path, deadline: float) -> int | None:
    """Bytes under path from stat alone (no file is opened); None once the deadline passes."""
    if not path.is_dir():
        try:
            return path.lstat().st_size
        except OSError:
            return None
    total, stack = 0, [path]
    while stack:
        if time.monotonic() > deadline:  # shortcut: checked per folder, not per file
            return None
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def human_size(size: int | None) -> str:
    if size is None:
        return "size unknown"
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def mentioned(primary: Path, primary_branch: str, names: list[str], shas: list[str]) -> set[str]:
    """Names in a tracked file, or in a commit message of shas not on origin/<primary_branch>."""
    if not names:
        return set()
    # One grep for every name (patterns on stdin); matched lines only, so the cost is the repo's size.
    grep = git(primary, "grep", "-I", "-h", "-F", "-f", "-", input="\n".join(names) + "\n")
    text = grep.stdout if grep.returncode == 0 else ""
    if shas:  # shortcut: the newest DOC_COMMITS commits, not all of them; raise it if bundles go unseen
        log = git(primary, "log", "--format=%B", f"-n{DOC_COMMITS}", *shas, "--not",
                  f"origin/{primary_branch}")
        text += log.stdout if log.returncode == 0 else ""
    return {name for name in names if name in text}


def bundle_action(strategy: Config, root: str, kind: str) -> str:
    """The saved strategy's action for a bundle of this root and class; empty with no strategy."""
    if not strategy:
        return ""
    return "keep" if root in strategy.get("roots", {}) else BUNDLE_ACTION[kind]


def doc_rows(
    primary: Path, cfg: Config, trees: list[Row], tiers: dict[Tip, Verdict],
    deadline: float | None = None,
) -> list[Row]:
    """Untracked doc-root bundles: class, tickets and live/linked/unlinked; size for linked."""
    # Any worktree counts, by branch or folder name, so a detached Codex worktree does too.
    # A branch without one counts until it lands: landed local branches are never deleted.
    unlanded = {tip for tip, (tier, _) in tiers.items() if tier not in VERIFIED}
    names = [n for t in trees[1:] for n in (t["branch"], Path(t["path"]).name) if n]
    names += [n for _, n in unlanded if n]
    carried = {t for name in names for t in ticket_ids(cfg, name)}
    deadline = budget(deadline)
    worktrees = [Path(tree["path"]).resolve() for tree in trees[1:]]
    bundles = [
        (root, rel) for root, rel in doc_entries(primary, cfg)
        # a worktree, or a folder holding worktrees, is not a document bundle
        if not any(w.is_relative_to((primary / rel).resolve()) for w in worktrees)
    ]
    # A bundle's path is specific; its bare name only when it carries a ticket ("archive" is not).
    names = {posixpath.basename(rel) for _, rel in bundles}
    named = mentioned(primary, cfg["primary_branch"],
                      sorted({rel for _, rel in bundles} | {n for n in names if ticket_ids(cfg, n)}),
                      sorted({sha for sha, _ in unlanded}))
    rows, strategy = [], cfg["doc_strategy"]
    for root, rel in bundles:
        name, path = posixpath.basename(rel), primary / rel
        tickets = list(dict.fromkeys(ticket_ids(cfg, name) + top_file_tickets(cfg, path)))
        words = cfg["doc_reference"].get(root, DOC_REFERENCE)
        live = rel in named or name in named or carried & set(tickets)
        state = "live" if live else "linked" if tickets else "unlinked"
        kind = "reference" if any(w.lower() in name.lower() for w in words) else "ephemeral"
        rows.append({
            "path": rel,
            "root": root,
            "class": kind,
            "action": bundle_action(strategy, root, kind),
            "state": state,
            "tickets": tickets,
            "size": tree_size(path, deadline) if state == "linked" else None,
        })
    return rows


def doc_line(doc: Row) -> str:
    return (f"doc {doc['class']} {doc['path']}: tickets {', '.join(doc['tickets'])};"
            f" {human_size(doc['size'])}")


def linked_docs(report: Row) -> list[Row]:
    return [d for d in report["docs"] if d["state"] == "linked"]


def trash_dir(primary: Path) -> Path:
    # Inside .git: never tracked, never untracked, never in a worktree removal check.
    return Path(git(primary, "rev-parse", "--absolute-git-dir").stdout.strip()) / "lumberjack-trash"


def grace_wait(primary: Path, strategy: Config, marked: list[Row]) -> list[Row]:
    """The marked bundles still inside grace_days; records the first date a session start saw each.

    The state file holds {bundle path: first UTC date seen linked}. Anything unreadable counts as
    empty, so a damaged file only restarts the count; a bundle no longer marked loses its date."""
    state, today = trash_dir(primary).with_name("lumberjack-docs.json"), datetime.now(timezone.utc).date()
    try:
        seen = {k: min(date.fromisoformat(v), today) for k, v in json.loads(state.read_text("utf-8")).items()}
    except (OSError, ValueError, AttributeError, TypeError, RecursionError):
        seen = {}
    kept = {d["path"]: seen.get(d["path"], today) for d in marked}
    if kept != seen:
        try:
            state.write_text(json.dumps({k: v.isoformat() for k, v in kept.items()}), "utf-8")
        except OSError:  # without a date the bundle waits; grace_days = 0 needs none
            pass
    return [d for d in marked if (today - kept[d["path"]]).days < strategy["grace_days"]]


def trash_rows(primary: Path, cfg: Config, deadline: float | None = None) -> list[Row]:
    """Trash folders older than the saved retention, oldest first."""
    days, folder = cfg["doc_strategy"].get("trash_days"), trash_dir(primary)
    if not days or not folder.is_dir():
        return []
    now, rows = datetime.now(timezone.utc), []
    deadline = budget(deadline)
    for name in sorted(os.listdir(folder)):
        try:
            age = (now - datetime.strptime(name, TRASH_STAMP).replace(tzinfo=timezone.utc)).days
        except ValueError:  # not ours
            continue
        if age >= days:
            rows.append({"name": name, "days": age, "size": tree_size(folder / name, deadline)})
    return rows


def trash_line(row: Row) -> str:
    return f"trash {row['name']}: {row['days']} days old; {human_size(row['size'])}"


def build_report(
    primary: Path, cfg: Config, stale: bool = False, deadline: float | None = None
) -> Row:
    deadline = budget(deadline)
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
        "docs": doc_rows(primary, cfg, trees, tiers, deadline),
        "doc_strategy": cfg["doc_strategy"],
        "trash": trash_rows(primary, cfg, deadline),
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


def fresh(path: Path, branch: str, head: str) -> bool:
    """Added under FRESH_HOURS ago, and its branch has not moved since it was created."""
    admin = git(path, "rev-parse", "--absolute-git-dir").stdout.strip()
    try:  # git writes commondir once, when the worktree is added
        age = time.time() - Path(admin, "commondir").stat().st_mtime
    except OSError:
        return False
    if not branch or age > FRESH_HOURS * 3600:
        return False
    log = lines(git(path, "reflog", "show", "--format=%H %gs", f"refs/heads/{branch}"))
    if not log:  # some tools (the Claude desktop app) create branches without a reflog entry
        return True  # and any later commit or reset would have written one
    if len(log) > 1:  # moved since it was created, even when reset back to that commit
        return False
    sha, _, subject = log[0].partition(" ")
    return sha == head and subject.startswith("branch: Created from")


def short_path(primary: Path, path: str | Path) -> str:
    """Path relative to the primary's parent directory, else with home as ~."""
    full = Path(path).resolve()
    for base, prefix in ((primary.parent.resolve(), ""), (Path.home().resolve(), "~/")):
        if full.is_relative_to(base):
            return prefix + full.relative_to(base).as_posix()
    return full.as_posix()


def block_reason(
    primary: Path, cfg: Config, w: Row, here: Path | None, *, explicit: bool = False
) -> str:
    """Why a worktree stays, or an empty string; explicit is a named --remove of any tier."""
    evidence = cfg["evidence_dir"] and Path(w["path"]) / cfg["evidence_dir"]
    if same_dir(w["path"], primary):
        return "primary checkout"
    if w["branch"].startswith(cfg["prototype_prefix"]):
        return "prototype"
    on_branch = ("for-each-ref", "--contains", w["head"], "--count=1", "refs/heads")
    if explicit and not w["branch"] and not lines(git(primary, *on_branch)):
        return "no branch"  # removal would orphan the commits of a detached tip
    if here is not None and same_dir(w["path"], here):
        return "current checkout"  # never pull the directory from under the running session
    if w.get("locked"):
        return "locked"
    if w["dirty"]:  # -1 (status failed) counts as dirty
        return "dirty"
    # A new branch from origin/main looks integrated and pushed; its session may have just begun.
    if fresh(Path(w["path"]), w["branch"], w["head"]):
        return "fresh"
    # landed-pr: the forge saw this exact tip, and delete-on-merge drops the upstream.
    # Explicit: the kept branch holds the commits, so unpushed is no loss; ignored files are.
    if not explicit and w["tier"] != "landed-pr" and unpushed(Path(w["path"])):
        return "unpushed"
    if evidence and evidence.is_dir() and any(evidence.iterdir()):
        return "evidence"
    # Tracked files come back with any worktree; only untracked or ignored ones would be lost.
    if cfg["doc_roots"]:
        found = untracked_docs(Path(w["path"]), cfg["doc_roots"])
        if found is None or found:  # a failed listing keeps the worktree
            return "docs"
    return "ignored files" if explicit and stray_ignored(Path(w["path"])) else ""


def tag(primary: Path, w: Row) -> str:
    return f"{w['branch'] or '(detached)'} {short_path(primary, w['path'])}"


def worktree_event(primary: Path, cfg: Config, w: Row, here: Path | None) -> tuple[str, str]:
    """(count key or empty, output line) for a verified-landed worktree outside keep mode."""
    if reason := block_reason(primary, cfg, w, here):
        return "blocked", f"blocked {tag(primary, w)}: {reason}"
    if cfg["cleanup"] == "report":
        return "", f"landed {tag(primary, w)}: would remove"
    return remove_worktree(primary, w)


def remove_worktree(primary: Path, w: Row) -> tuple[str, str]:
    notes = ["local branch kept"] if w["branch"] else []
    if (gone := git(primary, "worktree", "remove", w["path"])).returncode:
        # On Windows a locked directory fails the remove after git deleted the files and entry.
        if any(same_dir(t["path"], w["path"]) for t in registered(primary)):
            return "blocked", f"blocked {tag(primary, w)}: {gone.stderr.strip()}"
        notes += ["directory left"] if Path(w["path"]).exists() else []
    return "cleaned", f"cleaned {tag(primary, w)}" + (f" ({'; '.join(notes)})" if notes else "")


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
    if reason := block_reason(primary, cfg, w, here, explicit=True):
        return False, f"blocked {tag(primary, w)}: {reason}"
    outcome, line = remove_worktree(primary, w)
    return outcome == "cleaned", line


def clear_bundles(start: Path, targets: list[str], promoted: str = "") -> list[tuple[bool, str]]:
    """Trash linked bundles as the saved strategy says; (done, output line) each."""
    primary = find_primary(start)
    cfg = load_config(primary)
    if promoted:
        fetch(primary)  # the promotion may have merged after this session's fetch
    docs = {d["path"]: d for d in build_report(primary, cfg)["docs"]}
    stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP)  # one trash folder per call
    return [clear_bundle(primary, cfg, docs, target, promoted, stamp) for target in targets]


def clear_bundle(
    primary: Path, cfg: Config, docs: dict[str, Row], target: str, promoted: str, stamp: str,
    deadline: float | None = None,
) -> tuple[bool, str]:
    """Trash one bundle; (done, output line). Past a deadline the scan stops with nothing moved: (False, "")."""
    strategy, rel = cfg["doc_strategy"], posixpath.normpath(target.replace("\\", "/"))
    doc = docs.get(rel)
    action = doc["action"] if doc else ""
    saved = False
    if action == "promote" and promoted.startswith(cfg["prototype_prefix"]):  # a pushed prototype
        copy = git(primary, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{promoted}")
        saved = copy.returncode == 0
    elif action == "promote" and promoted:  # ... or a document tracked on the primary branch
        base = f"origin/{cfg['primary_branch']}"
        saved = git(primary, "cat-file", "-t", f"{base}:{promoted}").stdout.strip() == "blob"
    reason = (
        "no strategy saved" if not strategy
        else "not a bundle" if doc is None
        else doc["state"] if doc["state"] != "linked"  # live or unlinked
        else "kept by strategy" if action == "keep"
        else "promote it first (--promoted)" if action == "promote" and not promoted
        else "the promoted copy is not on origin" if action == "promote" and not saved
        else ""
    )
    if reason:
        return False, f"blocked {rel}: {reason}"
    path, root = primary / rel, primary.resolve()
    # A link (symlink or Windows junction) can lead a bundle or an entry in it out of the
    # repository; move takes a link nested in a bundle as a link, so the target survives.
    outside, inner = not path.resolve().is_relative_to(root), []
    # rglob("*") yields a nested link itself on every platform (Windows also walks into junctions).
    for entry in path.rglob("*") if strategy["scope"] == "markdown" and path.is_dir() else ():
        if deadline is not None and time.monotonic() > deadline:
            return False, ""  # nothing has moved: the next session start scans it again
        inner.append(entry)
        outside = outside or not entry.resolve().is_relative_to(root)
    # shortcut: markdown scope matches the .md suffix only; widen it if other text formats matter.
    units = [path] if strategy["scope"] == "bundle" else [
        f for f in (inner if path.is_dir() else [path]) if f.suffix.lower() == ".md"
    ]
    if outside:
        return False, f"blocked {rel}: links outside the repository"
    if not units:
        return False, f"blocked {rel}: {NO_MARKDOWN}"
    for unit in units:  # only the purge deletes; a cleared bundle always goes to the trash
        dest = trash_dir(primary) / stamp / unit.relative_to(primary)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(unit, dest)
    return True, f"cleared {rel}: trashed {stamp}"


def purge_trash(start: Path) -> list[str]:
    """Delete the trash folders past retention; one line each."""
    primary = find_primary(start)
    cfg = load_config(primary)
    expired = trash_rows(primary, cfg)
    for row in expired:
        shutil.rmtree(trash_dir(primary) / row["name"], **{RMTREE_HANDLER: make_writable})
    return [f"purged {row['name']}" for row in expired] or ["trash: nothing past retention"]


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


def trash_docs(primary: Path, cfg: Config, report: Row, deadline: float) -> tuple[int, list[str]]:
    """Trash the linked bundles the strategy marks for trash; (count, lines).

    Stops at the time budget; the rest wait for the next session start."""
    marked = [d for d in linked_docs(report) if d["action"] == "trash"]
    if cfg["cleanup"] == "keep":
        return 0, []
    waiting = grace_wait(primary, cfg["doc_strategy"], marked)
    out = list(map(doc_line, waiting))  # inside grace_days: the normal doc line, not trashed or counted
    due = [d for d in marked if d not in waiting]
    if cfg["cleanup"] == "report":
        return 0, out + [f"doc {d['class']} {d['path']}: would trash" for d in due]
    docs = {d["path"]: d for d in report["docs"]}
    stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP)
    gone = []
    for doc in due:
        if time.monotonic() > deadline:
            break
        try:
            moved, line = clear_bundle(primary, cfg, docs, doc["path"], "", stamp, deadline)
        except (OSError, RuntimeError) as error:  # the session start never raises
            # RuntimeError: a symlink loop in resolve(); an OSError message would carry an absolute path
            reason = getattr(error, "strerror", None) or "cannot read the bundle"
            moved, line = False, f"blocked {doc['path']}: {reason}"
        if moved:
            gone.append(doc)
        if line and not line.endswith(NO_MARKDOWN):  # nothing left in scope: not worth repeating
            out.append(line)
    if gone:  # a trashed bundle loses its date, so one restored from the trash starts over
        grace_wait(primary, cfg["doc_strategy"], [d for d in marked if d not in gone])
    return len(gone), out


def run_cleanup(
    primary: Path, cfg: Config, report: Row, stale: bool, here: Path | None = None,
    deadline: float | None = None,
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
    out += map(doc_line, (d for d in linked_docs(report) if d["action"] != "trash"))
    trashed, doc_lines = trash_docs(primary, cfg, report, budget(deadline))
    counts["blocked"] += sum(line.startswith("blocked") for line in doc_lines)
    out += doc_lines
    out += map(trash_line, report["trash"])
    summary = (
        f"sweep: cleaned {counts['cleaned']}, landed {counts['landed']},"
        f" dependents {counts['dependents']}, blocked {counts['blocked']},"
        f" unverified {counts['unverified']}, orphans {len(report['orphans'])},"
        f" docs {trashed}"
        + (f", trash {len(report['trash'])}" if report["trash"] else "")
        + (", origin stale" if stale else "")
    )
    return [summary, *out]


HOOK_BEGIN, HOOK_END = "# >>> lumberjack", "# <<< lumberjack"  # around setup's hook block
HOOK_MARKERS = (HOOK_BEGIN, HOOK_END)


def package_has(primary: Path, key: str) -> bool:
    try:
        data = json.loads((primary / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and key in data


# Manager: (whether the repository uses it, the files setup writes its post-merge run to);
# detection goes in this order. Every such run names the script by lumberjack.script.
HOOK_MANAGERS: dict[str, tuple[Callable[[Path], bool], tuple[str, ...]]] = {
    "husky": (lambda p: (p / ".husky").is_dir(), (".husky/post-merge",)),
    "lefthook": (
        lambda p: (p / "lefthook.yml").is_file() or (p / ".lefthook.yml").is_file(),
        ("lefthook.yml", ".lefthook.yml", "lefthook-local.yml"),
    ),
    "pre-commit": (
        lambda p: (p / ".pre-commit-config.yaml").is_file(), (".pre-commit-config.yaml",)
    ),
    "simple-git-hooks": (lambda p: package_has(p, "simple-git-hooks"), ("package.json",)),
}


def hook_manager(primary: Path) -> str:
    """The git hook manager the repository already uses, or an empty string."""
    return next((name for name, (used, _) in HOOK_MANAGERS.items() if used(primary)), "")


def manager_wired(primary: Path) -> bool:
    for _, files in HOOK_MANAGERS.values():
        for rel in files:
            try:
                text = (primary / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "lumberjack.script" in text:
                return True
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
    if hook.is_file() and HOOK_BEGIN in hook.read_text(encoding="utf-8", errors="replace") and not hooks_path:
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
        deadline = budget(None)  # one budget for every doc step of this run
        report = build_report(primary, cfg, stale, deadline)
        return [*run_cleanup(primary, cfg, report, stale, here, deadline), *notes]
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
    parser.add_argument(
        "--clear", metavar="PATH", nargs="+",
        help="trash these linked doc bundles as doc_strategy says; exit 1 when one is blocked",
    )
    parser.add_argument(
        "--promoted", metavar="REF", default="",
        help="with --clear: a doc tracked on origin's primary branch, or a pushed prototype"
        " branch, holding a reference bundle's findings",
    )
    parser.add_argument("--purge-trash", action="store_true", help="delete trash past retention")
    args = parser.parse_args(argv)
    if args.clear is not None or args.purge_trash:
        try:
            if args.purge_trash:
                print("\n".join(purge_trash(Path.cwd())))
                return 0
            results = clear_bundles(Path.cwd(), args.clear, args.promoted)
        except ConfigError as error:
            raise SystemExit(error) from error
        print("\n".join(line for _, line in results))
        return 0 if all(cleared for cleared, _ in results) else 1
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
