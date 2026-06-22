#!/usr/bin/env bash
set -euo pipefail
if ! tmux list-sessions >/dev/null 2>&1; then
  echo "no tmux sessions"
  exit 0
fi
for session in $(tmux list-sessions -F '#S' | grep '^hermes-aicompany' || true); do
  echo "--- $session ---"
  tmux capture-pane -t "$session" -p | tail -30
  echo
done
