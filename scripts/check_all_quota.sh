#!/bin/bash
# === AI Company Quota Checker (Hierarchical) ===
# Priority: Monthly > Weekly > Hourly
# If higher tier exhausted, lower tier availability doesn't matter
#
# Secrets are intentionally not stored in this tracked script.
# Provide these via environment or an ignored .env.local file:
#   OPENCODE_AUTH_COOKIE=<cookie copied from dashboard request>
#   OPENCODE_WORKSPACE_ID="wrk_..."

set -u

QUOTA_ENV_FILE="${AI_AGENT_QUOTA_ENV_FILE:-.env.local}"
if [ -n "$QUOTA_ENV_FILE" ] && [ -f "$QUOTA_ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$QUOTA_ENV_FILE"
    set +a
fi

echo "=== $(date '+%H:%M:%S') ==="

# --- OpenCode Go ---
AUTH_COOKIE="${OPENCODE_AUTH_COOKIE:-}"
WORKSPACE_ID="${OPENCODE_WORKSPACE_ID:-}"

if [ -n "$AUTH_COOKIE" ] && [ -n "$WORKSPACE_ID" ]; then
    RESPONSE=$(curl -s "https://opencode.ai/workspace/${WORKSPACE_ID}/go"       -H 'accept: text/html' -H 'user-agent: Mozilla/5.0'       -b "$AUTH_COOKIE" 2>/dev/null)
else
    RESPONSE=""
fi

GO_ROLLING=$(echo "$RESPONSE" | grep -oP 'rollingUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1 | grep -oP 'usagePercent:\K\d+')
GO_WEEKLY=$(echo "$RESPONSE" | grep -oP 'weeklyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1 | grep -oP 'usagePercent:\K\d+')
GO_MONTHLY=$(echo "$RESPONSE" | grep -oP 'monthlyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1 | grep -oP 'usagePercent:\K\d+')
GO_ROLLING_RESET=$(echo "$RESPONSE" | grep -oP 'rollingUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1 | grep -oP 'resetInSec:\K\d+')
GO_WEEKLY_RESET=$(echo "$RESPONSE" | grep -oP 'weeklyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1 | grep -oP 'resetInSec:\K\d+')
GO_MONTHLY_RESET=$(echo "$RESPONSE" | grep -oP 'monthlyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1 | grep -oP 'resetInSec:\K\d+')

# --- Codex ---
CODEX=$(codexbar usage --provider codex --source oauth --format json 2>/dev/null)
CX_HOURLY=$(echo "$CODEX" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['usage']['primary']['usedPercent'])" 2>/dev/null)
CX_WEEKLY=$(echo "$CODEX" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['usage']['secondary']['usedPercent'])" 2>/dev/null)
CX_MONTHLY=$(echo "$CODEX" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]['usage']; print(d.get('tertiary',{}).get('usedPercent','N/A'))" 2>/dev/null)
CX_HOURLY_RESET=$(echo "$CODEX" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['usage']['primary']['resetDescription'])" 2>/dev/null)
CX_WEEKLY_RESET=$(echo "$CODEX" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['usage']['secondary']['resetDescription'])" 2>/dev/null)

# --- Hierarchical Check ---
sec_to_hms() { printf "%dh %dm" $((${1:-0}/3600)) $((${1:-0}%3600/60)); }

check_provider() {
    local name=$1 monthly=${2:-?} weekly=${3:-?} hourly=${4:-?} m_reset=${5:-0} w_reset=${6:-0} h_reset=${7:-?}
    local status="✅"
    local reason=""

    if [ "$monthly" = "?" ] || [ "$weekly" = "?" ] || [ "$hourly" = "?" ]; then
        status="⚪ UNKNOWN"
        reason="usage unavailable"
    elif [ "$monthly" -ge 100 ] 2>/dev/null; then
        status="🔴 BLOCKED"
        reason="Monthly ${monthly}% (reset: $(sec_to_hms "$m_reset"))"
    elif [ "$weekly" -ge 100 ] 2>/dev/null; then
        status="🔴 BLOCKED"
        reason="Weekly ${weekly}% (reset: $(sec_to_hms "$w_reset"))"
    elif [ "$hourly" -ge 100 ] 2>/dev/null; then
        status="🟡 WAIT"
        reason="Hourly ${hourly}% (reset: ${h_reset})"
    elif [ "$monthly" -ge 85 ] 2>/dev/null; then
        status="🟡 LOW"
        reason="Monthly ${monthly}%"
    elif [ "$weekly" -ge 85 ] 2>/dev/null; then
        status="🟡 LOW"
        reason="Weekly ${weekly}%"
    elif [ "$hourly" -ge 70 ] 2>/dev/null; then
        status="🟡 LOW"
        reason="Hourly ${hourly}%"
    fi
    echo "$status $name: M:${monthly}% W:${weekly}% H:${hourly}% $reason"
}

echo ""
check_provider "📦 Go" "${GO_MONTHLY:-?}" "${GO_WEEKLY:-?}" "${GO_ROLLING:-?}" "${GO_MONTHLY_RESET:-0}" "${GO_WEEKLY_RESET:-0}" "${GO_ROLLING_RESET:-?}"
check_provider "🤖 Codex" "${CX_MONTHLY:-0}" "${CX_WEEKLY:-?}" "${CX_HOURLY:-?}" "0" "0" "${CX_HOURLY_RESET:-?}"

# --- Work Decision ---
GO_OK=false; CX_OK=false
[ "${GO_MONTHLY:-100}" != "?" ] && [ "${GO_MONTHLY:-100}" -lt 100 ] 2>/dev/null && [ "${GO_WEEKLY:-100}" -lt 100 ] 2>/dev/null && [ "${GO_ROLLING:-100}" -lt 100 ] 2>/dev/null && GO_OK=true
[ "${CX_MONTHLY:-0}" != "?" ] && [ "${CX_MONTHLY:-0}" -lt 100 ] 2>/dev/null && [ "${CX_WEEKLY:-100}" -lt 100 ] 2>/dev/null && [ "${CX_HOURLY:-100}" -lt 100 ] 2>/dev/null && CX_OK=true

echo ""
if $GO_OK && $CX_OK; then echo "✅ Both available";
elif $GO_OK; then echo "⚠️  Only OpenCode Go available - use Go for work";
elif $CX_OK; then echo "⚠️  Only Codex available - use Codex for work";
else echo "🔴 All blocked or unavailable - wait/check credentials"; fi
