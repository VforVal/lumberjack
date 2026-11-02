"""Set up lumberjack in a repository: `discover` reports facts, `apply` writes the answers."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

if sys.version_info < (3, 11):  # before tomllib: a plain message instead of a traceback
    sys.exit(f"lumberjack: Python 3.11+ required, found {sys.version.split()[0]}")
import tomllib
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sweep_worktrees import (
    CACHE_DIRS,
    DEFAULTS,
    HOOK_BEGIN,
    HOOK_END,
    HOOK_MARKERS,
    LOCAL_CONFIG,
    SETUP_STAMP,
    ConfigError,
    config_table,
    find_primary,
    git,
    hook_manager,
    hook_script,
    lines,
    load_config,
    local_config,
    origin_default_branch,
    origin_parts,
    parse_config,
    plugin_version,
    registered,
)

Facts = dict[str, Any]
REMOTE = "refs/remotes/origin/"
TICKET = re.compile(r"\b([a-z]+-)\d+")
TRACKER_DOC = Path("docs", "agents", "issue-tracker.md")  # the matt-pocock-skills convention
# The block goes to the first that exists, else AGENTS.md; .claude/CLAUDE.md is Claude Code's too.
INSTRUCTION_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md")
LOCAL_INSTRUCTIONS = "CLAUDE.local.md"  # Claude Code's personal, uncommitted instructions file
SCOPES = ("team", "local")
DOC_SCAN = 200  # directory entries examined per ignored folder
NOT_DOCS = CACHE_DIRS | {".claude", ".codex"}  # tool caches and harness folders, never doc roots


def git_out(cwd: Path, *args: str) -> str:
    result = git(cwd, *args)
    return result.stdout.strip() if result.returncode == 0 else ""


def origin_facts(repo: Path) -> Facts:
    facts: Facts = {"url": None, "owner": None, "name": None, "default_branch": None}
    if not (url := git_out(repo, "remote", "get-url", "origin")):
        return facts
    _, owner, name = origin_parts(url)
    facts.update(url=url, owner=owner or None, name=name or None)
    branch = origin_default_branch(repo)
    if git(repo, "rev-parse", "--verify", "-q", REMOTE + branch).returncode == 0:
        facts["default_branch"] = branch
    return facts


def remote_facts(repo: Path) -> Facts:
    names = [
        ref.removeprefix(REMOTE)
        for ref in lines(git(repo, "for-each-ref", "--format=%(refname)", REMOTE))
    ]
    names = [name for name in names if name != "HEAD"]
    shapes: Counter[str] = Counter()
    for name in names:
        head, _, _ = name.rpartition("/")
        shapes[(TICKET.sub("<ticket>", head) + "/" if head else "") + "*"] += 1
    prefixes = Counter(m.group(1) for name in names if (m := TICKET.search(name)))
    owners = Counter(name.split("/")[0] for name in names if "/" in name)
    return {
        "remote_shapes": [{"shape": shape, "count": shapes[shape]} for shape in by_count(shapes)],
        "ticket_prefix_guess": by_count(prefixes)[0] if prefixes else "",
        "owners_guess": by_count(owners),
    }


def by_count(counter: Counter[str]) -> list[str]:
    """Keys, most frequent first, ties alphabetical."""
    return sorted(counter, key=lambda key: (-counter[key], key))


def worktree_parents(repo: Path) -> list[str]:
    """Folders holding linked worktrees: relative inside the repo, ~-based under home.

    A folder that holds the repo itself (home, ~/Documents) is left out: as a root it would
    match every project next to this one."""
    parents = {Path(tree["path"]).parent for tree in registered(repo)[1:]}
    return sorted(root_text(repo, p) for p in parents if not repo.is_relative_to(p))


def root_text(repo: Path, parent: Path) -> str:
    if parent.is_relative_to(repo):
        return parent.relative_to(repo).as_posix()
    if parent.is_relative_to(Path.home()):
        return "~/" + parent.relative_to(Path.home()).as_posix()
    return parent.as_posix()


def tracker_facts(repo: Path) -> Facts:
    doc = repo / TRACKER_DOC
    if not doc.is_file():
        return {"doc": None, "ticket_key": None, "prefix": None}
    key = re.search(r"\b[A-Z]{2,}-\d+", doc.read_text(encoding="utf-8", errors="replace"))
    prefix = re.match(r"[A-Z]+", key.group()).group().lower() + "-" if key else None
    return {"doc": str(doc), "ticket_key": key.group() if key else None, "prefix": prefix}


def has_markdown(folder: Path) -> bool:
    """Whether a .md name shows up in the first DOC_SCAN entries; names only, no file reads."""
    # shortcut: a huge folder whose Markdown sits past the budget is missed, raise DOC_SCAN then.
    pending, seen = [folder], 0
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if (seen := seen + 1) > DOC_SCAN:
                        return False
                    if entry.name.lower().endswith(".md"):
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
        except OSError:
            continue
    return False


def doc_root_candidates(repo: Path) -> list[Facts]:
    found = {}
    if repo.joinpath(".scratch").is_dir():
        found[".scratch"] = "conventional scratch folder for agent work documents"
    holders = set()  # top-level folders that hold worktrees, whose checkouts carry Markdown too
    for tree in registered(repo)[1:]:
        try:
            holders.add(Path(tree["path"]).resolve().relative_to(repo.resolve()).parts[0])
        except (ValueError, IndexError):
            pass
    # --directory prints an ignored folder as one line without descending into it.
    ignored = git_out(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    for line in ignored.splitlines():
        name = line.rstrip("/")
        if line.endswith("/") and "/" not in name and name not in NOT_DOCS | found.keys():
            if name in holders:
                found[name] = "ignored folder holding worktrees; name its document subfolders instead"
            elif has_markdown(repo / name):
                found[name] = "ignored folder holding Markdown"
    return [{"path": path, "reason": reason} for path, reason in sorted(found.items())]


def hook_facts(repo: Path, hooks_path: str | None) -> Facts | None:
    if hooks_path:
        base = repo / hooks_path
    else:
        base = Path(git_out(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
    path = base / "post-merge"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    first = text.splitlines()[0] if text else ""
    has_marker = HOOK_BEGIN in text
    if has_marker:
        first = re.match(r"(?:[ \t]*(?:#(?!\!)[^\n]*)?\n)*(.*)", text).group(1)
    return {
        "path": str(path),
        "is_sh": first.startswith("#!") and "sh" in first,
        "has_marker": has_marker,
    }


def gh_facts() -> Facts:
    if not (gh := shutil.which("gh")):
        return {"present": False, "authenticated": None}
    try:
        ok = subprocess.run([gh, "auth", "status"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return {"present": True, "authenticated": None}
    return {"present": True, "authenticated": ok}


def config_facts(repo: Path) -> Facts:
    try:
        source, _ = config_table(repo)
        if not source:
            return {"source": None, "error": None}
        return {**load_config(repo), "source": source, "error": None}
    except ConfigError as error:
        return {"source": error.source or None, "error": str(error)}


def discover(repo: Path, started_in: Path) -> Facts:
    """Facts about the primary checkout repo; started_in is the checkout setup was run from."""
    hooks_path = git_out(repo, "config", "--type=path", "core.hooksPath") or None
    has = repo.joinpath
    return {
        "repo": str(repo),
        "started_in": None if started_in == repo else str(started_in),
        "name": repo.name,
        "instructions": instructions_facts(repo),
        "harness": {
            "claude_md": has("CLAUDE.md").is_file() or has(".claude", "CLAUDE.md").is_file(),
            "agents_md": has("AGENTS.md").is_file(),
            "claude_settings": has(".claude", "settings.json").is_file(),
            "codex_dir": has(".codex").is_dir(),
        },
        "origin": origin_facts(repo),
        **remote_facts(repo),
        "worktree_parents": worktree_parents(repo),
        "tracker": tracker_facts(repo),
        "hook_manager": hook_manager(repo) or None,
        "post_merge_hook": hook_facts(repo, hooks_path),
        "hooks_path": hooks_path,
        "git_version": git_out(repo, "--version").removeprefix("git version "),
        "python": sys.version.split()[0],
        "gh": gh_facts(),
        "config": config_facts(repo),
        "doc_root_candidates": doc_root_candidates(repo),
    }


def instructions_facts(repo: Path) -> Facts:
    """Where apply writes the instructions block for a team setup, and whether that file is a link."""
    target = next((name for name in INSTRUCTION_FILES if (repo / name).is_file()), "AGENTS.md")
    path = repo / target
    return {
        "target": target,
        "symlink": path.is_symlink(),
        "links_to": os.path.relpath(path.resolve(), repo) if path.is_symlink() else None,
    }


CONFIG_DOC = {
    "owners": "Branch owners allowed in <owner>/<slug> names; empty allows any owner.",
    "ticket_prefix": "Ticket prefix in branch names (for example eng-); empty means no ticket.",
    "prototype_prefix": "Prefix of throwaway prototype branches, which skip the owner rule.",
    "primary_branch": "Trunk branch the primary checkout stays on; landed work merges into it.",
    "evidence_dir": "Directory whose files protect a worktree from cleanup; empty for none.",
    "doc_roots": "Folders for work documents; a worktree holding an untracked or ignored file there stays.",
    "worktree_roots": "Where worktrees live: ~ is the home directory, * a glob, {name} the repo name.",
    "cleanup": "What SessionStart does with landed worktrees: prune, report or keep.",
    "stop_gate": "Whether the Stop hook blocks on branch and primary-checkout debt.",
    "doc_strategy": "What /lumberjack:cleanup may do with doc bundles; empty until the maintainer answers once.",
    "doc_reference": "Per doc root, folder-name words that make a bundle reference (* for all: a durable root); default research, prototype.",
}
HARNESSES = ("claude", "codex")
ANSWER_KEYS = {*DEFAULTS, "harnesses", "hook", "pin_claude_settings", "scope", "instructions"}


def abort(message: str) -> SystemExit:
    print(f"setup: {message}", file=sys.stderr)
    return SystemExit(2)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):  # an inline table
        return "{" + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def refuse_escape(repo: Path, path: Path) -> None:
    """A link inside the repo must not redirect a write elsewhere; a hooksPath outside it may."""
    inside = Path(os.path.abspath(path)).is_relative_to(os.path.abspath(repo))
    if inside and not path.resolve().is_relative_to(repo.resolve()):
        raise abort(f"{path} resolves outside the repository; refusing to write")


def write_file(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    refuse_escape(repo, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {rel}")


def check_answers(answers: Any, repo: Path) -> Facts:
    if not isinstance(answers, dict):
        raise abort("answers must be a JSON object")
    if unknown := sorted(answers.keys() - ANSWER_KEYS):
        raise abort("unknown answer key: " + ", ".join(unknown))
    optional = {"harnesses": [], "pin_claude_settings": False, "scope": "team", "instructions": ""}
    trunk = {"primary_branch": origin_default_branch(repo)}  # an omitted trunk is origin's, not "main"
    full = {"hook": "plain", **optional, **DEFAULTS, **trunk, **answers}
    for key, default in optional.items():
        if not isinstance(full[key], type(default)):
            raise abort(f"{key} must be {type(default).__name__}")
    if not set(full["harnesses"]) <= set(HARNESSES):
        raise abort("harnesses must be a list of " + " and/or ".join(HARNESSES))
    if full["hook"] not in HOOKS:
        raise abort("hook must be one of " + ", ".join(HOOKS))
    if full["scope"] not in SCOPES:
        raise abort("scope must be one of " + ", ".join(SCOPES))
    if full["scope"] == "local" and full["pin_claude_settings"]:
        raise abort("pin_claude_settings needs scope team: pinning prompts every teammate")
    if full["scope"] == "local" and full["hook"] not in ("plain", "none"):
        raise abort("scope local needs hook plain or none: a hook manager's files are committed")
    return full


def config_text(answers: Facts) -> str:
    keys = (f"# {CONFIG_DOC[k]}\n{k} = {toml_value(answers[k])}\n" for k in DEFAULTS)
    text = f"{SETUP_STAMP}{plugin_version()}\n\n" + "\n".join(keys)
    try:  # validate through the parser sweep uses
        parse_config(tomllib.loads(text), ".lumberjack.toml")
    except tomllib.TOMLDecodeError as error:
        raise abort(f"lumberjack: bad TOML in .lumberjack.toml: {error}") from error
    except ConfigError as error:
        raise abort(str(error)) from error
    return text


MD_MARKERS = ("<!-- >>> lumberjack -->", "<!-- <<< lumberjack -->")
LEGACY_RUN = 'uv run --quiet --script "$(git config --get lumberjack.script)" --cleanup'
# First of python3, python that reports 3.11+; empty when none does. No quotes: pre-commit
# wraps it in sh -c '...'.
PYTHON = (
    'for python in python3 python; do case $($python -V 2>/dev/null) in '
    'Python?3.1[1-9]*|Python?3.[2-9][0-9]*) break;; esac; python=; done'
)
RUN = '"$python" "$(git config --get lumberjack.script)" --cleanup'
# A sweep failure must never fail the merge.
SAFE_RUN = (
    'test -f "$(git config --get lumberjack.script)" && '
    f'{PYTHON} && test -n "$python" && {RUN} || true'
)
BLOCK = f"""{HOOK_BEGIN}
script="$(git config --get lumberjack.script)"
{PYTHON}
if [ -f "$script" ] && [ -n "$python" ]; then
  "$python" "$script" --cleanup || true
fi
{HOOK_END}
"""
# The instructions block must not promise a cleanup or a gate the config turned off.
SESSION_START = {
    "prune": "Session start cleans up landed worktrees",
    "report": "Session start only reports what cleanup would remove (`cleanup = \"report\"`)",
    "keep": "Session start leaves landed worktrees alone (`cleanup = \"keep\"`)",
}
STOP_HOOK = {True: "the Stop hook gates debt", False: "the Stop gate is off"}
INSTALL_STEPS = {
    "claude": (
        "claude plugin marketplace add VforVal/lumberjack",
        "claude plugin install lumberjack@lumberjack",
    ),
    "codex": (
        "codex plugin marketplace add VforVal/lumberjack",
        "codex plugin add lumberjack@lumberjack",
        "then run /hooks once in Codex to trust the plugin hooks",
    ),
}


def marked(markers: tuple[str, str]) -> re.Pattern[str]:
    """The lumberjack block between the markers, with its surrounding line ends."""
    begin, end = map(re.escape, markers)
    return re.compile(rf"^[ \t]*{begin}.*?{end}[^\n]*\n?", re.S | re.M)


def merge_block(
    text: str, block: str, markers: tuple[str, str] = HOOK_MARKERS, after: str | None = None
) -> str:
    """Replace the marked block, else insert it after the `after` line, else append it."""
    span = marked(markers)
    if span.search(text):
        return span.sub(lambda _: block, text, count=1)
    if after and (found := re.search(after, text, re.M)):
        return text[: found.end()] + block + text[found.end() :]
    # shortcut: a trailing `exit` in an existing hook hides the appended block; no parsing, fix by hand.
    return text.rstrip("\n") + "\n\n" + block if text.strip() else block


def read_json(repo: Path, rel: str) -> Facts | None:
    try:
        data = json.loads((repo / rel).read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError as error:
        raise abort(f"{rel} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise abort(f"{rel} is not a JSON object")
    return data


def write_json(repo: Path, rel: str, data: Facts) -> None:
    write_file(repo, rel, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_merged(
    repo: Path,
    rel: str,
    block: str,
    start: str = "",
    markers: tuple[str, str] = HOOK_MARKERS,
    after: str | None = None,
) -> None:
    path = repo / rel
    text = path.read_bytes().decode("utf-8") if path.is_file() else start
    write_file(repo, rel, merge_block(text, block, markers, after))


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


def plain_hook(repo: Path) -> None:
    hooks_path = git_out(repo, "config", "--type=path", "core.hooksPath")
    if found := hook_facts(repo, hooks_path or None):
        path = Path(found["path"])
        refuse_escape(repo, path)  # before the rename below can move a foreign hook
        text = path.read_text(encoding="utf-8", errors="replace")
        if not found["is_sh"]:
            kept = path.with_name("post-merge.user")
            if kept.exists():
                raise abort(f"{kept} already exists; move it aside and re-run")
            path.replace(kept)
            print(f"ran rename {path.name} to {kept.name}")
            text = '#!/bin/sh\n"$(dirname "$0")/post-merge.user" "$@"\n'
        write_file(repo, path.as_posix(), merge_block(text, BLOCK))
        return
    rel = f"{hooks_path or '.githooks'}/post-merge"
    write_merged(repo, rel, BLOCK, start="#!/bin/sh\n")
    make_executable(repo / rel)
    if not hooks_path:
        git(repo, "config", "core.hooksPath", ".githooks")
        print("ran git config core.hooksPath .githooks")


def husky_hook(repo: Path) -> None:
    write_merged(repo, ".husky/post-merge", BLOCK, start="#!/bin/sh\n")
    make_executable(repo / ".husky" / "post-merge")


def lefthook_hook(repo: Path) -> None:
    # Always the local file: lefthook merges it into lefthook.yml, and the plain hook
    # cannot coexist (lefthook install refuses core.hooksPath and renames .git/hooks/post-merge).
    job = f"""{HOOK_BEGIN}
post-merge:
  jobs:
    - name: lumberjack
      run: {SAFE_RUN}
{HOOK_END}
"""
    write_merged(repo, "lefthook-local.yml", job)
    print("note: run lefthook install to activate the hook")


def pre_commit_hook(repo: Path) -> None:
    path = repo / ".pre-commit-config.yaml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not re.search(r"^repos:[ \t]*$", text, re.M):
        text = (text.rstrip("\n") + "\n" if text.strip() else "") + "repos:\n"
    item = re.search(r"^( *)- +repo:", text, re.M)
    i = item.group(1) if item else "  "
    entry = f"sh -c '{SAFE_RUN}'"
    block = f"""{i}{HOOK_BEGIN}
{i}- repo: local
{i}  hooks:
{i}    - id: lumberjack
{i}      name: lumberjack
{i}      entry: {entry}
{i}      language: system
{i}      stages: [post-merge]
{i}      always_run: true
{i}      pass_filenames: false
{i}{HOOK_END}
"""
    text += "" if text.endswith("\n") else "\n"
    write_file(repo, path.name, merge_block(text, block, after=r"^repos:[ \t]*\n"))
    print("note: run pre-commit install --hook-type post-merge to activate the hook")


def simple_git_hooks_hook(repo: Path) -> None:
    package = read_json(repo, "package.json") or {}
    hooks = package.setdefault("simple-git-hooks", {})
    old = hooks.get("post-merge", "")
    # Upgrade both the original command and its later missing-script guard.
    legacy_safe = f'test -f "$(git config --get lumberjack.script)" && {LEGACY_RUN} || true'
    old = old.replace(legacy_safe, SAFE_RUN).replace(f"{LEGACY_RUN} || true", SAFE_RUN)
    if RUN in old:
        hooks["post-merge"] = old if SAFE_RUN in old else old.replace(f"{RUN} || true", SAFE_RUN)
    else:
        hooks["post-merge"] = f"{old}; {SAFE_RUN}" if old else SAFE_RUN
    write_json(repo, "package.json", package)
    print("note: run npx simple-git-hooks to activate the hook")


def worktree_ignores(roots: list[str]) -> list[str]:
    inside = (
        r.removeprefix("./").rstrip("/")
        for r in roots
        if not (r.startswith(("~", "/")) or Path(r).is_absolute() or ".." in Path(r).parts)
        and "{" not in r
    )
    return [f"{r}/" for r in inside if r]


HOOK_WRITERS = {
    "plain": plain_hook,
    "husky": husky_hook,
    "lefthook": lefthook_hook,
    "pre-commit": pre_commit_hook,
    "simple-git-hooks": simple_git_hooks_hook,
}
HOOKS = (*HOOK_WRITERS, "none")


def write_hook(repo: Path, writer: Callable[[Path], None]) -> None:
    writer(repo)
    script = hook_script(find_primary(repo))
    git(repo, "config", "lumberjack.script", script)
    print(f"ran git config lumberjack.script {script}")


def write_settings(repo: Path, settings: Facts) -> None:
    settings.setdefault("extraKnownMarketplaces", {})["lumberjack"] = {
        "source": {"source": "github", "repo": "VforVal/lumberjack"},
        "autoUpdate": False,
    }
    settings.setdefault("enabledPlugins", {})["lumberjack@lumberjack"] = True
    write_json(repo, ".claude/settings.json", settings)


def instructions(answers: Facts) -> str:
    """The CLAUDE.md/AGENTS.md block, saying what this config's session start and Stop hook do."""
    return f"""{MD_MARKERS[0]}
## Lumberjack

Worktree rules live in the `lumberjack` skill; config in `.lumberjack.toml`.
{SESSION_START[answers["cleanup"]]}; {STOP_HOOK[answers["stop_gate"]]}.
Re-run the `lumberjack:setup` skill after conventions change.
{MD_MARKERS[1]}
"""


def instructions_target(answers: Facts, repo: Path) -> str:
    """The file that gets the instructions block; never a symbolic link, which may be shared."""
    default = LOCAL_INSTRUCTIONS if answers["scope"] == "local" else instructions_facts(repo)["target"]
    target = answers["instructions"] or default
    path = repo / target
    if Path(target).is_absolute() or ".." in Path(target).parts:
        raise abort(f"instructions must be a path inside the repository: {target}")
    if path.is_symlink():
        linked = os.path.relpath(path.resolve(), repo)
        raise abort(f"{target} is a symbolic link to {linked}; set instructions to another file")
    return target


def write_instructions(repo: Path, target: str, block: str) -> None:
    write_merged(repo, target, block, markers=MD_MARKERS)


def write_ignores(repo: Path, rel: str, entries: list[str]) -> None:
    """Append the entries missing from the ignore file at rel."""
    ignore = repo / rel
    text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    have = text.splitlines()
    if new := [x for x in entries if x not in have]:
        sep = "" if text.endswith("\n") or not text else "\n"
        write_file(repo, rel, text + sep + "".join(f"{x}\n" for x in new))


def local_rel(repo: Path, path: Path) -> str:
    """path relative to the primary checkout when inside it, as apply prints it; else absolute."""
    return path.relative_to(repo).as_posix() if path.is_relative_to(repo) else str(path)


def local_plain_hook(repo: Path) -> None:
    """The plain hook in the git common dir's hooks folder; core.hooksPath is left alone."""
    if hook_facts(repo, None):
        plain_hook(repo)  # appends to the existing hook there
        return
    path = local_config(repo).parent / "hooks" / "post-merge"
    write_merged(repo, local_rel(repo, path), BLOCK, start="#!/bin/sh\n")
    make_executable(path)


def apply(answers: Facts, repo: Path) -> None:
    """Write the answers into the primary checkout repo: committed files (team) or untracked ones (local)."""
    config, hook = config_text(answers), answers["hook"]
    local = answers["scope"] == "local"
    settings = None  # every read that can abort runs before the first write
    if answers["pin_claude_settings"] and "claude" in answers["harnesses"]:
        settings = read_json(repo, ".claude/settings.json") or {}
    if hook == "simple-git-hooks" and read_json(repo, "package.json") is None:
        raise abort("simple-git-hooks needs a package.json")
    if local and hook == "plain" and git_out(repo, "config", "core.hooksPath"):
        raise abort("scope local cannot write a hook under core.hooksPath (it is committed); use hook none")
    target = instructions_target(answers, repo)
    ignores = worktree_ignores(answers["worktree_roots"])
    git_dir = local_config(repo).parent
    write_file(repo, local_rel(repo, git_dir / "lumberjack.toml") if local else ".lumberjack.toml", config)
    if hook != "none":
        write_hook(repo, local_plain_hook if local and hook == "plain" else HOOK_WRITERS[hook])
    if local:
        untracked = [target] if git(repo, "ls-files", "--error-unmatch", target).returncode else []
        write_ignores(repo, local_rel(repo, git_dir / "info" / "exclude"), [*ignores, *untracked])
    else:
        write_ignores(repo, ".gitignore", ignores)
    if settings is not None:
        write_settings(repo, settings)
    write_instructions(repo, target, instructions(answers))
    for harness in HARNESSES:
        if harness in answers["harnesses"]:
            for line in INSTALL_STEPS[harness]:
                print(f"printed, not run: {line}")
    if not (gh := gh_facts())["present"] or not gh["authenticated"]:
        print("gh missing or unauthenticated: landed detection is offline-only until gh auth login")


def save_strategy(strategy: Any, repo: Path) -> None:
    """Write a doc strategy into the config, keeping every other saved value."""
    try:
        source, table = config_table(repo)
        table.pop("doc_strategy", None)  # replaced whole, so an old-style one never blocks its own fix
        target = local_rel(repo, local_config(repo)) if source == LOCAL_CONFIG else ".lumberjack.toml"
        write_file(repo, target, config_text({**parse_config(table, source), "doc_strategy": strategy}))
    except ConfigError as error:
        raise abort(str(error)) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    find = sub.add_parser("discover", help="print repository facts as JSON")
    put = sub.add_parser("apply", help="write config, hook, settings and instructions")
    put.add_argument("answers", type=Path, help="JSON file of setup answers")
    keep = sub.add_parser("strategy", help="save the doc strategy in the config")
    keep.add_argument("strategy", type=Path, help="JSON file: scope, trash_days, grace_days, roots")
    for command in (find, put, keep):
        command.add_argument(
            "--repo", type=Path, help="repository path (default: the current repository)"
        )
    args = parser.parse_args(argv)
    try:
        top = git_out(args.repo or Path.cwd(), "rev-parse", "--show-toplevel")
    except FileNotFoundError as error:
        raise abort("git missing: install git from https://git-scm.com/downloads and re-run") from error
    if not top:
        raise SystemExit(f"setup: not a git repository: {args.repo or Path.cwd()}")
    started_in = Path(top)
    repo = find_primary(started_in)  # the hooks read config from the primary checkout only
    if args.command == "apply":
        try:
            answers = check_answers(json.loads(args.answers.read_text(encoding="utf-8")), repo)
        except (OSError, ValueError) as error:
            raise abort(f"cannot read answers: {error}") from error
        apply(answers, repo)
        return 0
    if args.command == "strategy":
        try:
            save_strategy(json.loads(args.strategy.read_text(encoding="utf-8")), repo)
        except (OSError, ValueError) as error:
            raise abort(f"cannot read strategy: {error}") from error
        return 0
    print(json.dumps(discover(repo, started_in), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
