#!/usr/bin/env bash
set -euo pipefail
for session in $(tmux list-sessions -F '#S' 2>/dev/null | grep '^hermes-aicompany' || true); do
  tmux kill-session -t "$session"
  echo "stopped: $session"
done
