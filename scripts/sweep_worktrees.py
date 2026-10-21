"""Inventory worktrees, orphan directories, branches and the primary checkout."""

import argparse
import contextlib
import dataclasses
import functools
import glob
import json
import math
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
from collections.abc import Callable, Iterator
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
SIZE_SECONDS = 3  # sizing bundles, timed from when sizing starts
DOC_SECONDS = 7  # trashing and purging, timed after the report; with fetch and gh it fits the hook's 30
PURGE_SECONDS = 3  # the end of DOC_SECONDS kept for purging, so trashing never starves it
NO_MARKDOWN = "no Markdown in scope"
TOO_LARGE = "too large to walk in time"
DOC_STATE = "lumberjack-docs.json"  # next to the trash: first-seen dates and skipped bundles
DOC_LOCK = "lumberjack-docs.lock"  # next to the trash: OS-locked while one run trashes or purges
DOC_WAIT = 10  # seconds --clear and --purge-trash wait for a held lock; session start never waits
BUSY = "a sweep is trashing"
TALLY = "lumberjack-tally.jsonl"  # next to the trash: one JSON line per session start, trashed bundle and purge


def reparse(st: os.stat_result) -> bool:
    """A Windows reparse point that redirects: a symlink or a junction (is_junction is 3.12+); never on POSIX.

    The name-surrogate bit, as Python's own link test uses: OneDrive's cloud folders are reparse
    points without it, real folders to walk into."""
    return bool(
        getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        and getattr(st, "st_reparse_tag", 0) & 0x20000000
    )


def is_folder(entry: os.DirEntry[str]) -> bool:
    """A real directory: not a symlink, and not a Windows junction."""
    return entry.is_dir(follow_symlinks=False) and not reparse(entry.stat(follow_symlinks=False))


def walk(path: str, deadline: float = math.inf) -> Iterator[os.DirEntry[str]]:
    """Every entry under path, each folder after its contents; TimeoutError once the deadline passes.

    A link (symlink or Windows junction) is a leaf, never followed, so each file is found once."""
    with os.scandir(path) as entries:  # shortcut: one folder is listed before the first check; split listing if one folder holds millions
        children = list(entries)
    for entry in children:
        if time.monotonic() > deadline:
            raise TimeoutError
        if is_folder(entry):
            yield from walk(entry.path, deadline)
        yield entry


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
        isinstance(words, list) and all(isinstance(w, str) and w for w in words)
        for words in cfg["doc_reference"].values()
    ):  # an empty word is in every name, so it would make a whole root reference by accident
        raise ConfigError("lumberjack: doc_reference must map each root to a list of non-empty words", source)
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
            " trash_days (at least 1) and grace_days (0 or more), optionally roots"
            " (each root = \"keep\")", source,
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
    """Bytes under path from stat alone (no file is opened); None past the deadline or unreadable."""
    stats = file_stats(path, deadline)
    return stats and stats[1]


def file_stats(path: Path, deadline: float, suffix: str = "") -> tuple[int, int] | None:
    """(files, bytes) under path whose names end in suffix, any case; None past the deadline or unreadable."""
    try:
        if not path.is_dir():
            return (1, path.lstat().st_size) if path.name.lower().endswith(suffix) else (0, 0)
        found = [e.stat(follow_symlinks=False).st_size for e in walk(str(path), deadline)
                 if not is_folder(e) and e.name.lower().endswith(suffix)]
    except (OSError, RecursionError):  # TimeoutError is an OSError; a tree deeper than walk recurses
        return None
    return len(found), sum(found)


def files_text(stats: tuple[int, int] | None) -> str:
    return "files uncounted" if stats is None else f"{plural(stats[0], 'file')} ({human_size(stats[1])})"


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


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


def worktree_name(primary: Path, path: str) -> str:
    """A worktree's folder name; its parent's in the Codex layout `<dir>/<ticket slug>/<repo name>`."""
    leaf = Path(path)
    return leaf.parent.name if leaf.name.casefold() == primary.name.casefold() else leaf.name


def bundle_class(cfg: Config, root: str, name: str) -> str:
    """`reference` when a doc_reference word is in the bundle's folder name, else `ephemeral`.

    The word `*` matches every bundle, so a whole root can hold durable documents.
    """
    words = cfg["doc_reference"].get(root, DOC_REFERENCE)
    return "reference" if any(w == "*" or w.lower() in name.lower() for w in words) else "ephemeral"


def doc_rows(primary: Path, cfg: Config, trees: list[Row], tiers: dict[Tip, Verdict]) -> list[Row]:
    """Untracked doc-root bundles: class, tickets and live/linked/unlinked; size for linked."""
    # Any worktree counts, by branch or folder name, so a detached Codex worktree does too.
    # A branch without one counts until it lands: landed local branches are never deleted.
    unlanded = {tip for tip, (tier, _) in tiers.items() if tier not in VERIFIED}
    names = [n for t in trees[1:] for n in (t["branch"], worktree_name(primary, t["path"])) if n]
    names += [n for _, n in unlanded if n]
    carried = {t for name in names for t in ticket_ids(cfg, name)}
    deadline = time.monotonic() + SIZE_SECONDS
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
        live = rel in named or name in named or carried & set(tickets)
        state = "live" if live else "linked" if tickets else "unlinked"
        kind = bundle_class(cfg, root, name)
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


@dataclasses.dataclass
class Footprint:
    """Bundles or trash folders a step moves or deletes, with the files and bytes under them."""

    items: int = 0
    files: int = 0
    size: int = 0
    uncounted: int = 0  # items past the counting budget, or unreadable: the files are a lower bound

    def add(self, stats: tuple[int, int] | None) -> None:
        self.items += 1
        if stats is None:
            self.uncounted += 1
        else:
            self.files, self.size = self.files + stats[0], self.size + stats[1]

    def text(self, noun: str) -> str:
        """`3 bundles (120 files, 4.2 MB)`; `at least` when an item went uncounted."""
        if not self.items:
            return plural(0, noun)
        if self.uncounted == self.items:
            return f"{plural(self.items, noun)} (files uncounted)"
        floor = "at least " if self.uncounted else ""
        return f"{plural(self.items, noun)} ({floor}{plural(self.files, 'file')}, {human_size(self.size)})"


@dataclasses.dataclass(frozen=True)
class Target:
    """A bundle or trash folder report mode lists: its line, its key for --files, and the files in scope."""

    line: str
    key: str  # the bundle's repository path or the trash folder's timestamp
    path: Path
    suffix: str = ""  # ".md" in Markdown scope


@dataclasses.dataclass(frozen=True)
class FileList:
    """--files: list every file of all bundles and trash folders, or only of those named in keys."""

    on: bool = False
    keys: frozenset[str] = frozenset()

    def picks(self, key: str) -> bool:
        return self.on and (not self.keys or key in self.keys)

    def missing(self, seen: set[str]) -> list[str]:
        """A line for each named key that matched no bundle or trash folder."""
        return [f"files: nothing named {key}" for key in sorted(self.keys - seen)]


@dataclasses.dataclass
class DocRun:
    """One run's doc work: bundles trashed and its lines; in report mode, what it would trash and purge."""

    trashed: int = 0
    lines: list[str] = dataclasses.field(default_factory=list)
    trash: Footprint = dataclasses.field(default_factory=Footprint)
    purge: Footprint = dataclasses.field(default_factory=Footprint)
    targets: list[Target] = dataclasses.field(default_factory=list)  # report mode, for --files

    def add_purge(self, other: "DocRun") -> None:
        self.purge, self.lines, self.targets = other.purge, self.lines + other.lines, self.targets + other.targets


@functools.cache  # the git folder never moves during a run; one git call, not one per file
def trash_dir(primary: Path) -> Path:
    # Inside .git: never tracked, never untracked, never in a worktree removal check.
    found = git(primary, "rev-parse", "--absolute-git-dir")
    if found.returncode:  # an empty path would put the trash under the current directory
        raise OSError(f"cannot find the git folder: {found.stderr.strip()}")
    return Path(found.stdout.strip()) / "lumberjack-trash"


def doc_state(primary: Path) -> tuple[dict[str, date], set[str]]:
    """The first UTC date a session start saw each bundle linked, and the skipped bundles.

    A skipped bundle has nothing left in scope, or is too large to walk in time, so session start
    leaves it (to the cleanup command). Anything unreadable counts as empty, so a damaged file only
    restarts the count."""
    try:
        saved = json.loads(trash_dir(primary).with_name(DOC_STATE).read_text("utf-8"))
        return {k: date.fromisoformat(v) for k, v in saved["since"].items()}, set(saved["skipped"])
    except (OSError, ValueError, AttributeError, TypeError, KeyError, RecursionError):
        return {}, set()


def save_doc_state(primary: Path, since: dict[str, date], skipped: set[str]) -> None:
    saved = {"since": {k: v.isoformat() for k, v in since.items()}, "skipped": sorted(skipped)}
    try:
        trash_dir(primary).with_name(DOC_STATE).write_text(json.dumps(saved), "utf-8")
    except OSError:  # without a date the bundle waits; grace_days = 0 needs none
        pass


def record(primary: Path, event: Row) -> None:
    """Append one event, stamped with the UTC time, to the tally; a failed write only loses the count."""
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with trash_dir(primary).with_name(TALLY).open("a", encoding="utf-8") as file:
            file.write(json.dumps({"at": at, **event}) + "\n")
    except OSError:
        pass


def recorded(primary: Path) -> list[Row]:
    """The tally's events, oldest first; a damaged line is skipped."""
    try:
        text = trash_dir(primary).with_name(TALLY).read_text("utf-8")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("at"), str):
            events.append(event)
    return events


def lock_file(fd: int, take: bool = True) -> bool:
    """Take (or drop) this process's lock on fd without waiting; the OS drops it when a run dies."""
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)
    except OSError:  # held by another run
        return False
    return True


@contextlib.contextmanager
def doc_work(primary: Path, wait: float = 0) -> Iterator[Row | None]:
    """Hold the doc lock for one run that trashes or purges, with the saved doc state.

    Yields {"since": first-seen dates, "skipped": bundles}, saved on a normal exit when it
    changed, or None while another run still holds the lock after `wait` seconds. A lock file
    this run cannot open raises OSError: an unwritable git folder is not a busy one."""
    give_up = time.monotonic() + wait
    fd = os.open(trash_dir(primary).with_name(DOC_LOCK), os.O_CREAT | os.O_RDWR)
    try:
        while not lock_file(fd):
            if time.monotonic() >= give_up:
                yield None
                return
            time.sleep(0.2)
        try:
            seen = doc_state(primary)
            state = {"since": dict(seen[0]), "skipped": set(seen[1])}
            yield state
            if (state["since"], state["skipped"]) != seen:
                save_doc_state(primary, state["since"], state["skipped"])
        finally:
            lock_file(fd, take=False)
    finally:
        os.close(fd)


def trash_rows(primary: Path, cfg: Config) -> list[Row]:
    """Trash folders older than the saved retention, oldest first."""
    days = cfg["doc_strategy"].get("trash_days")
    if not days or not (folder := trash_dir(primary)).is_dir():
        return []
    now, rows = datetime.now(timezone.utc), []
    for name in sorted(os.listdir(folder)):
        try:
            age = (now - datetime.strptime(name, TRASH_STAMP).replace(tzinfo=timezone.utc)).days
        except ValueError:  # not ours
            continue
        if age >= days:
            rows.append({"name": name, "days": age})
    return rows


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
        "docs": doc_rows(primary, cfg, trees, tiers),
        "doc_strategy": cfg["doc_strategy"],
        "trash": trash_rows(primary, cfg),
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
        return "removable", f"landed {tag(primary, w)}: would remove"
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


def clear_bundles(start: Path, targets: list[str], promoted: str = "") -> list[tuple[str, str]]:
    """Trash linked bundles as the saved strategy says; (status, output line) each, as clear_bundle."""
    primary = find_primary(start)
    cfg = load_config(primary)
    if promoted:
        fetch(primary)  # the promotion may have merged after this session's fetch; never under the lock
    with doc_work(primary, DOC_WAIT) as state:  # taken before the report, so it sees what is left
        if state is None:
            return [("blocked", f"blocked {target}: {BUSY}") for target in targets]
        docs = {d["path"]: d for d in build_report(primary, cfg)["docs"]}
        stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP)  # one trash folder per call
        return [clear_bundle(primary, cfg, docs, target, promoted, stamp) for target in targets]


def clear_bundle(
    primary: Path, cfg: Config, docs: dict[str, Row], target: str, promoted: str, stamp: str,
    deadline: float = math.inf,
) -> tuple[str, str]:
    """Trash one bundle; (status, output line).

    Status: `cleared` (gone), `partial` (its Markdown moved, other files stay), `blocked`,
    `skip` (no Markdown in scope) or `timeout` (past the deadline, nothing moved, line empty)."""
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
        return "blocked", f"blocked {rel}: {reason}"
    path, root = primary / rel, primary.resolve()
    # A link (symlink or Windows junction) can lead a bundle out of the repository; move takes
    # a bundle cleared whole, and a link nested in it, as a link, so the target survives.
    if not path.resolve().is_relative_to(root):
        return "blocked", f"blocked {rel}: links outside the repository"
    # Resolved through its doc root only: a doc root may be a link, a Markdown-scope bundle may not.
    inner, bundle, markdown = [], path.parent.resolve() / path.name, strategy["scope"] == "markdown"
    if markdown and path.is_dir() and path.resolve() != bundle:
        return "blocked", f"blocked {rel}: links outside the bundle"
    # Markdown scope moves the files it finds, and a link that resolves elsewhere is not the
    # bundle's. Only links are resolved (a junction is a dir but no folder); a realpath per file
    # costs the budget.
    try:
        for entry in walk(str(path), deadline) if markdown and path.is_dir() else ():
            link = not is_folder(entry) and (entry.is_symlink() or entry.is_dir(follow_symlinks=False))
            if link and not Path(entry.path).resolve().is_relative_to(bundle):
                return "blocked", f"blocked {rel}: links outside the bundle"
            inner.append(entry)
    except TimeoutError:
        return "timeout", ""  # nothing has moved: the next session start scans it again
    files = [Path(e.path) for e in inner if not is_folder(e)] if path.is_dir() else [path]
    # shortcut: markdown scope matches the .md suffix only; widen it if other text formats matter.
    units = [path] if not markdown else [f for f in files if f.suffix.lower() == ".md"]
    if not units:
        return "skip", f"blocked {rel}: {NO_MARKDOWN}"
    folder = trash_dir(primary) / stamp
    for unit in units:  # only the purge deletes; a cleared bundle always goes to the trash
        dest = folder / unit.relative_to(primary)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(unit, dest)
    # Folders the move emptied go too, deepest first as walked; rmdir refuses any that still holds a file.
    for left in [e.path for e in inner if is_folder(e)] + [path]:
        try:
            os.rmdir(left)
        except OSError:
            pass
    record(primary, {"event": "trash", "stamp": stamp, "bundle": rel})
    return "partial" if path.exists() else "cleared", f"cleared {rel}: trashed {stamp}"


def remove(func: Callable[[str], object], path: str) -> None:
    try:
        func(path)
    except PermissionError:
        make_writable(func, path, None)


def purge_tree(path: str, deadline: float, deleted: Footprint) -> bool:
    """Delete path bottom-up, counting each file in deleted; False once the deadline passes.

    The rest is left for a later run."""
    try:
        for entry in walk(path, deadline):  # a link is a leaf: unlink removes the link, not its target
            if is_folder(entry):
                remove(os.rmdir, entry.path)
            else:
                size = entry.stat(follow_symlinks=False).st_size
                remove(os.unlink, entry.path)
                deleted.files, deleted.size = deleted.files + 1, deleted.size + size
    except TimeoutError:
        return False
    remove(os.rmdir, path)
    return True


def purge_expired(primary: Path, rows: list[Row], deadline: float) -> list[str]:
    """Delete the expired trash folders, oldest first, until the deadline; one line each.

    A folder the deadline cuts short keeps its name (and so its age); a later run finishes it.
    The tally records the files each folder lost, finished or not."""
    out = []
    for row in rows:
        deleted, done, blocked = Footprint(), False, ""
        try:
            done = purge_tree(str(trash_dir(primary) / row["name"]), deadline, deleted)
        except (OSError, RuntimeError) as error:  # the session start never raises (RuntimeError: too deep)
            blocked = getattr(error, "strerror", None) or "cannot delete"
        if done or deleted.files:
            record(primary, {"event": "purge", "stamp": row["name"], "files": deleted.files, "size": deleted.size})
        if blocked:
            out.append(f"blocked trash {row['name']}: {blocked}")
        elif not done:
            break  # out of time: the rest waits for a later run
        else:
            out.append(f"purged {row['name']}")
    return out


def purge_trash(start: Path) -> list[str]:
    """Delete every trash folder past retention in one run; one line each."""
    primary = find_primary(start)
    cfg = load_config(primary)
    with doc_work(primary, DOC_WAIT) as state:
        if state is None:
            return [f"blocked trash: {BUSY}"]
        done = purge_expired(primary, trash_rows(primary, cfg), math.inf)
    return done or ["trash: nothing past retention"]


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


def trash_docs(primary: Path, cfg: Config, report: Row, state: Row, deadline: float) -> DocRun:
    """Trash the linked bundles the strategy marks for trash, updating doc_work's state.

    Stops at the time budget; the rest wait for the next session start. Report mode moves
    nothing and counts the files each due bundle would lose instead."""
    marked = [d for d in linked_docs(report) if d["action"] == "trash"]
    if cfg["cleanup"] == "keep":
        return DocRun()
    seen, skipped = state["since"], state["skipped"]
    today = datetime.now(timezone.utc).date()
    # One no longer marked is dropped; a future date (a clock that ran ahead) becomes today.
    since = {d["path"]: min(seen.get(d["path"], today), today) for d in marked}
    skip = skipped & since.keys()
    waiting = [d for d in marked if (today - since[d["path"]]).days < cfg["doc_strategy"]["grace_days"]]
    # inside grace_days: its doc line, not trashed or counted; a skipped one stays silent
    out = [doc_line(d) for d in waiting if d["path"] not in skip]
    due = [d for d in marked if d not in waiting and d["path"] not in skip]
    run = DocRun(lines=out)
    if cfg["cleanup"] == "report":
        suffix = ".md" if cfg["doc_strategy"]["scope"] == "markdown" else ""
        for d in due:
            stats = file_stats(primary / d["path"], deadline, suffix)
            run.trash.add(stats)
            out.append(line := f"doc {d['class']} {d['path']}: would trash {files_text(stats)}")
            run.targets.append(Target(line, d["path"], primary / d["path"], suffix))
        due = []
    docs = {d["path"]: d for d in report["docs"]}
    stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP)
    trashed, opened = 0, time.monotonic()
    for doc in due:
        if (started := time.monotonic()) > deadline:
            break
        try:
            status, line = clear_bundle(primary, cfg, docs, doc["path"], "", stamp, deadline)
        except (OSError, RuntimeError) as error:  # the session start never raises
            # RuntimeError: a symlink loop in resolve(); an OSError message would carry an absolute path
            reason = getattr(error, "strerror", None) or "cannot read the bundle"
            status, line = "blocked", f"blocked {doc['path']}: {reason}"
        # It had more than half of trashing's time and still ran out: it would every run,
        # so it is left to the cleanup command, said once. One that started late just waits.
        if status == "timeout" and deadline - started > (deadline - opened) / 2:
            skip.add(doc["path"])
            line = f"blocked {doc['path']}: {TOO_LARGE}"
        if status in ("cleared", "partial"):  # a trashed bundle loses its date: restored, it starts over
            trashed += 1
            del since[doc["path"]]
        # shortcut: a skipped bundle stays skipped while linked, so a new .md in it needs --clear;
        # recheck skipped bundles by mtime if new files in them turn out to matter.
        if status in ("partial", "skip"):
            skip.add(doc["path"])  # nothing left in scope: not worth walking again
        if line and status != "skip":
            out.append(line)
    state["since"], state["skipped"] = since, skip
    run.trashed = trashed
    return run


def sweep_docs(primary: Path, cfg: Config, report: Row) -> DocRun:
    """Session start's doc work: trash by rule, then purge, within DOC_SECONDS.

    Leaves docs alone while another run holds the doc lock: two at once would move the same files."""
    if not cfg["doc_strategy"] or cfg["cleanup"] == "keep":
        return DocRun()
    deadline = time.monotonic() + DOC_SECONDS  # started after the branch checks, so slow git or gh never eats it
    run = DocRun()
    try:
        with doc_work(primary) as state:
            if state is not None:
                run = trash_docs(primary, cfg, report, state, deadline - PURGE_SECONDS)
                if cfg["cleanup"] == "prune":
                    run.lines += purge_expired(primary, report["trash"], deadline)
    except OSError as error:  # the worktree lines above are still reported
        run.lines.append(f"docs: unavailable ({error})")
    if cfg["cleanup"] == "report":
        run.add_purge(would_purge(primary, report["trash"], deadline))
    return run


def preview_docs(primary: Path, cfg: Config, report: Row) -> DocRun:
    """Report mode's doc lines from the saved first-seen dates, which it never writes; counting has no time limit."""
    if not cfg["doc_strategy"]:
        return DocRun()
    since, skipped = doc_state(primary)
    run = trash_docs(primary, cfg, report, {"since": since, "skipped": skipped}, math.inf)
    run.add_purge(would_purge(primary, report["trash"], math.inf))
    return run


def would_purge(primary: Path, rows: list[Row], deadline: float) -> DocRun:
    """Report mode: the expired trash folders with the files each holds, counted until the deadline."""
    run = DocRun()
    for row in rows:
        folder = trash_dir(primary) / row["name"]
        stats = file_stats(folder, deadline)
        run.purge.add(stats)
        run.lines.append(line := f"trash {row['name']}: would purge {files_text(stats)}")
        run.targets.append(Target(line, row["name"], folder))
    return run


def file_lines(path: Path, base: Path, suffix: str = "") -> list[str]:
    """`file <path> (<size>)` for each file under path whose name ends in suffix, relative to base.

    One line saying why instead when path cannot be listed (gone, unreadable, too deep)."""
    try:
        if not path.is_dir():
            found = [(path.relative_to(base).as_posix(), path.lstat().st_size)]
            return [f"file {n} ({human_size(b)})" for n, b in found if n.lower().endswith(suffix)]
        cut = len(str(base)) + 1  # walk's paths all start with base: slicing beats relative_to per file
        sized = sorted(
            (e.path[cut:].replace(os.sep, "/"), e.stat(follow_symlinks=False).st_size)
            for e in walk(str(path)) if not is_folder(e) and e.name.lower().endswith(suffix)
        )
    except (OSError, RecursionError) as error:
        return [f"files: cannot list {path.relative_to(base).as_posix()} ({getattr(error, 'strerror', None) or 'too deep'})"]
    return [f"file {name} ({human_size(size)})" for name, size in sized]


DocStep = Callable[[Path, Config, Row], DocRun]


def run_cleanup(
    primary: Path, cfg: Config, report: Row, stale: bool, here: Path | None,
    docs: DocStep = sweep_docs,
) -> list[str]:
    """Remove verified-landed, clean, pushed worktrees (the branch stays); one line per event.

    Report mode adds a `sweep would:` line after the summary with what prune mode would do."""
    mode, trunk = cfg["cleanup"], cfg["primary_branch"]
    out: list[str] = []
    counts = dict.fromkeys(("cleaned", "removable", "landed", "dependents", "blocked", "unverified"), 0)
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
    run = docs(primary, cfg, report)
    counts["blocked"] += sum(line.startswith("blocked") for line in run.lines)
    out += run.lines
    summary = (
        f"sweep: cleaned {counts['cleaned']}, landed {counts['landed']},"
        f" dependents {counts['dependents']}, blocked {counts['blocked']},"
        f" unverified {counts['unverified']}, orphans {len(report['orphans'])},"
        f" docs {run.trashed}"
        + (", origin stale" if stale else "")
    )
    if mode != "report":
        return [summary, *out]
    caches = sum(o["kind"] == "node_modules-only" for o in report["orphans"])
    would = (
        f"sweep would: remove {plural(counts['removable'], 'worktree')},"
        f" delete {plural(caches, 'orphan')}, trash {run.trash.text('bundle')},"
        f" purge {run.purge.text('trash folder')}"
    )
    return [summary, would, *out]


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
    stale, skipped = fetch_trunk(primary, cfg["primary_branch"])
    if skipped:
        return [skipped, *notes]
    try:
        report = build_report(primary, cfg, stale)
        return [*run_cleanup(primary, cfg, report, stale, here), *notes]
    except OSError as error:
        return [f"sweep: unavailable ({error})", *notes]


def fetch_trunk(primary: Path, trunk: str) -> tuple[bool, str]:
    """Fetch origin when the primary checkout is on trunk; (origin stale, why the sweep skips or empty)."""
    if git(primary, "branch", "--show-current").stdout.strip() != trunk:
        return False, f"sweep: primary checkout is not on {trunk}; skipped"
    stale = fetch(primary)
    if git(primary, "rev-parse", "--verify", "-q", f"origin/{trunk}").returncode:
        return stale, f"sweep: origin/{trunk} is missing; skipped"
    return stale, ""


def preview(start: Path, listing: FileList = FileList()) -> list[str]:
    """What the next session start would remove, trash and purge, as `cleanup = "report"` lists it.

    Changes nothing but origin's refs (it fetches): no first-seen dates, no hook settings, no tally.
    Counts files without a time limit. listing picks the bundles (by path) and trash folders (by
    timestamp) whose every file is listed after their line."""
    primary = find_primary(start)
    cfg = load_config(primary)
    if cfg["cleanup"] == "keep":
        return ['sweep would: nothing; cleanup = "keep"']
    stale, skipped = fetch_trunk(primary, cfg["primary_branch"])
    if skipped:
        return [skipped]
    here = Path(git(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    report = build_report(primary, cfg, stale)
    runs: list[DocRun] = []

    def docs(*args: Any) -> DocRun:
        runs.append(preview_docs(*args))
        return runs[-1]

    out = run_cleanup(primary, {**cfg, "cleanup": "report"}, report, stale, here, docs)
    targets = [t for run in runs for t in run.targets]
    for target in (t for t in targets if listing.picks(t.key)):
        at = out.index(target.line) + 1
        base = trash_dir(primary) if target.path.is_relative_to(trash_dir(primary)) else primary
        out[at:at] = file_lines(target.path, base, target.suffix)
    return out + listing.missing({t.key for t in targets})


def tally(start: Path, listing: FileList = FileList()) -> list[str]:
    """Bundles trashed and trash purged since the last session start and in all, and what the trash holds.

    listing picks this session's bundles (by path) whose every file still in the trash is listed."""
    primary = find_primary(start)
    cfg = load_config(primary)
    events = recorded(primary)
    session, since = this_session(events)
    trash, deadline = trash_dir(primary), math.inf
    out, moved = [], Footprint()
    for e in session:
        if e.get("event") == "trash":
            path = trash / str(e.get("stamp")) / str(e.get("bundle"))
            held = path.exists()  # gone once purged, or moved back out to restore it
            stats = file_stats(path, deadline) if held else None
            moved.add(stats)
            where = f"{files_text(stats)}, in {e.get('stamp')}" if held else "no longer in the trash"
            out.append(f"trashed {e.get('bundle')}: {where}")
            out += file_lines(path, trash) if held and listing.picks(str(e.get("bundle"))) else []
    folders = Footprint()
    for name in sorted(os.listdir(trash)) if trash.is_dir() else []:
        if re.fullmatch(r"\d{8}T\d{6}Z", name):  # a TRASH_STAMP name; anything else is not ours
            folders.add(file_stats(trash / name, deadline))
    bundles = sum(e.get("event") == "trash" for e in events)
    first = f" (since {events[0]['at']})" if events else ""
    return [
        f"tally: since the last session start ({since}): trashed {moved.text('bundle')}, purged {purged(session)}",
        f"tally: in all{first}: trashed {plural(bundles, 'bundle')}, purged {purged(events)}",
        f"tally: in the trash: {folders.text('folder')}, {len(trash_rows(primary, cfg))} past retention",
        *out,
        *listing.missing({str(e.get("bundle")) for e in session if e.get("event") == "trash"}),
    ]


def this_session(events: list[Row]) -> tuple[list[Row], str]:
    """(events after the latest session's first start marker, that marker's time), in the file's order.

    Resume, /clear and compaction fire SessionStart again with the same session id, so the window
    opens at that id's first marker; work other sessions did on this clone since then counts too.
    A marker without an id stands alone."""
    starts = [i for i, e in enumerate(events) if e.get("event") == "session"]
    if not starts:
        return events, "none recorded"
    sid = events[starts[-1]].get("id")
    first = next(i for i in starts if i == starts[-1] or sid and events[i].get("id") == sid)
    return events[first + 1:], events[first]["at"]


def purged(events: list[Row]) -> str:
    """`N files (size)` the purge events among events deleted."""
    done = [e for e in events if e.get("event") == "purge"]
    files = sum(e["files"] for e in done if isinstance(e.get("files"), int))
    size = sum(e["size"] for e in done if isinstance(e.get("size"), int))
    return files_text((files, size))


def mark_session(start: Path, session_id: str) -> None:
    """Record a session start in the tally, so --tally can tell this session's work from earlier work.

    Only with a saved doc_strategy: without one nothing is ever trashed, so there is nothing to tally."""
    try:
        primary = find_primary(start)
        if load_config(primary)["doc_strategy"]:
            record(primary, {"event": "session", "id": session_id})
    except (SystemExit, ConfigError):  # not a git repository, or a config the session start reports
        pass


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
        mark_session(Path.cwd(), str(payload.get("session_id", "")))
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
    parser.add_argument(
        "--preview", action="store_true",
        help="list what the next session start would remove, trash and purge, with file counts; change nothing",
    )
    parser.add_argument(
        "--tally", action="store_true",
        help="count bundles trashed and files purged this session and in all, and what the trash holds",
    )
    parser.add_argument(
        "--files", metavar="KEY", nargs="*",
        help="with --preview or --tally: list every file of these bundle paths or trash timestamps, or of all",
    )
    args = parser.parse_args(argv)
    if args.files is not None and not (args.preview or args.tally):
        parser.error("--files needs --preview or --tally")
    if args.preview or args.tally:
        keys = frozenset(posixpath.normpath(k.replace("\\", "/")) for k in args.files or ())
        listing = FileList(args.files is not None, keys)
        try:
            print("\n".join(preview(Path.cwd(), listing) if args.preview else tally(Path.cwd(), listing)))
        except (ConfigError, OSError) as error:
            raise SystemExit(error) from error
        return 0
    if args.clear is not None or args.purge_trash:
        try:
            if args.purge_trash:
                lines = purge_trash(Path.cwd())
                print("\n".join(lines))
                return 1 if any(line.startswith("blocked") for line in lines) else 0
            results = clear_bundles(Path.cwd(), args.clear, args.promoted)
        except (ConfigError, OSError) as error:
            raise SystemExit(error) from error
        print("\n".join(line for _, line in results))
        return 0 if all(status in ("cleared", "partial") for status, _ in results) else 1
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
