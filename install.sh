#!/bin/sh
# Link the orchestrator command and skill into the user's tool directories.
# Safe to rerun; never replaces a real file or directory.
set -eu

repo=$(cd "$(dirname "$0")" && pwd -P)

link() { # link <target> <path>
  if [ -L "$2" ]; then
    rm "$2"
  elif [ -e "$2" ]; then
    echo "install: $2 exists and is not a symlink; move it away and rerun" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$2")"
  ln -s "$1" "$2"
  echo "linked $2 -> $1"
}

link "$repo/bin/orchestrator" "$HOME/.local/bin/orchestrator"
link "$repo/skills/orchestrator" "$HOME/.agents/skills/orchestrator"
# Codex reads ~/.agents/skills directly; Claude Code and Pi get links, like the herdr skill.
[ -d "$HOME/.claude" ] && link ../../.agents/skills/orchestrator "$HOME/.claude/skills/orchestrator"
[ -d "$HOME/.pi/agent" ] && link ../../../.agents/skills/orchestrator "$HOME/.pi/agent/skills/orchestrator"
exit 0
