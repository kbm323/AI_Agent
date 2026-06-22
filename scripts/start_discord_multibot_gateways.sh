#!/usr/bin/env bash
set -euo pipefail

profiles=(
  aicompanyceo
  aicompanyassistant
  aicompanycontent
  aicompanyart
  aicompanytech
  aicompanymarketing
  aicompanyquality
)

for profile in "${profiles[@]}"; do
  session="hermes-${profile}"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "already running: $session"
    continue
  fi
  tmux new-session -d -s "$session" -x 120 -y 40 \
    "HERMES_ACCEPT_HOOKS=1 hermes --profile $profile gateway run"
  echo "started: $session"
  sleep 1
done

echo
echo "status:"
tmux list-sessions 2>/dev/null | grep '^hermes-aicompany' || true
