#!/usr/bin/env bash
set -euo pipefail

profiles=(
  aicompanyassistant
  aicompanyceo
  aicompanycontent
  aicompanyart
  aicompanytech
  aicompanymarketing
  aicompanyquality
)

: "${ROLLBACK_STATE_DIR:?ROLLBACK_STATE_DIR is required}"
: "${DEPLOY_RECORD_DIR:?DEPLOY_RECORD_DIR is required}"
HERMES_PROFILE_ROOT="${HERMES_PROFILE_ROOT:-$HOME/.hermes/profiles}"

start_gateway() {
  local profile="$1"
  local session="hermes-${profile}"
  tmux kill-session -t "$session" 2>/dev/null || true
  tmux new-session -d -s "$session" -x 120 -y 40 \
    "HERMES_ACCEPT_HOOKS=1 hermes --profile $profile gateway run"
}

prepare_rollback() {
  local profile profile_root state_root session

  # Candidate processes may predate loaded markers; stop every profile first.
  for profile in "${profiles[@]}"; do
    session="hermes-${profile}"
    tmux kill-session -t "$session" 2>/dev/null || true
  done

  for profile in "${profiles[@]}"; do
    profile_root="$HERMES_PROFILE_ROOT/$profile"
    state_root="$ROLLBACK_STATE_DIR/$profile"
    test -d "$state_root"
    test -f "$state_root/was-running" || test -f "$state_root/was-stopped"

    hermes --profile "$profile" plugins disable ai-agent-commands || true
    hermes --profile "$profile" skills uninstall save || true

    rm -rf "$profile_root/plugins/ai-agent-commands" "$profile_root/skills/save"
    if [ -f "$state_root/config.yaml" ]; then
      mkdir -p "$profile_root"
      cp -a "$state_root/config.yaml" "$profile_root/config.yaml"
    elif [ -f "$state_root/config-was-absent" ]; then
      rm -f "$profile_root/config.yaml"
    else
      echo "missing prior config state: $profile" >&2
      exit 2
    fi

    mkdir -p "$profile_root/plugins" "$profile_root/skills"
    if [ -d "$state_root/plugin" ]; then
      cp -a "$state_root/plugin" "$profile_root/plugins/ai-agent-commands"
    fi
    if [ -d "$state_root/skill" ]; then
      cp -a "$state_root/skill" "$profile_root/skills/save"
    fi

    hermes --profile "$profile" plugins list \
      > "$DEPLOY_RECORD_DIR/$profile.plugins.rollback.txt"
    hermes --profile "$profile" skills list \
      > "$DEPLOY_RECORD_DIR/$profile.skills.rollback.txt"
  done

  # Resynchronize all seven against restored state; prior state is applied later.
  for profile in "${profiles[@]}"; do
    start_gateway "$profile"
  done
}

finalize_rollback() {
  local profile state_root session evidence

  for profile in "${profiles[@]}"; do
    state_root="$ROLLBACK_STATE_DIR/$profile"
    session="hermes-${profile}"
    evidence="$DEPLOY_RECORD_DIR/$profile.rollback-absence.txt"
    test -s "$evidence"
    grep -F "tool absent: save_discord_thread_to_obsidian" "$evidence"
    grep -F "picker absent: /archive" "$evidence"

    if [ -f "$state_root/was-running" ]; then
      if ! tmux has-session -t "$session" 2>/dev/null; then
        start_gateway "$profile"
      fi
    elif [ -f "$state_root/was-stopped" ]; then
      tmux kill-session -t "$session" 2>/dev/null || true
      ! tmux has-session -t "$session" 2>/dev/null
    else
      echo "missing prior gateway state: $profile" >&2
      exit 2
    fi
  done
}

case "${1:-}" in
  prepare) prepare_rollback ;;
  finalize) finalize_rollback ;;
  *)
    echo "usage: $0 <prepare|finalize>" >&2
    exit 2
    ;;
esac
