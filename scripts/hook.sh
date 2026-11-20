#!/bin/sh
unset GIT_DIR GIT_PREFIX
for p in python3 python; do
  case $($p -V 2>/dev/null) in
    Python?3.1[1-9]*|Python?3.[2-9][0-9]*) exec "$p" "$(dirname "$0")/sweep_worktrees.py" --hook ;;
  esac
done
exec python3 "$(dirname "$0")/sweep_worktrees.py" --hook
