#!/bin/bash
# opencode-go quota checker
# Usage: set OPENCODE_AUTH_COOKIE and OPENCODE_WORKSPACE_ID, then run ./scripts/check_quota.sh
# Or store those values in ignored .env.local.

set -u

QUOTA_ENV_FILE="${AI_AGENT_QUOTA_ENV_FILE:-.env.local}"
if [ -n "$QUOTA_ENV_FILE" ] && [ -f "$QUOTA_ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$QUOTA_ENV_FILE"
    set +a
fi

AUTH_COOKIE="${OPENCODE_AUTH_COOKIE:-}"
WORKSPACE_ID="${OPENCODE_WORKSPACE_ID:-}"

if [ -z "$AUTH_COOKIE" ] || [ -z "$WORKSPACE_ID" ]; then
    echo "=== OpenCode Go Usage ==="
    echo "⚪ UNKNOWN: set OPENCODE_AUTH_COOKIE and OPENCODE_WORKSPACE_ID in env or ignored .env.local"
    exit 0
fi

RESPONSE=$(curl -s "https://opencode.ai/workspace/${WORKSPACE_ID}/go"   -H 'accept: text/html'   -H 'user-agent: Mozilla/5.0'   -b "$AUTH_COOKIE" 2>/dev/null)

# Extract usage data from embedded JS state
ROLLING=$(echo "$RESPONSE" | grep -oP 'rollingUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1)
WEEKLY=$(echo "$RESPONSE" | grep -oP 'weeklyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1)
MONTHLY=$(echo "$RESPONSE" | grep -oP 'monthlyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1)

echo "=== OpenCode Go Usage ==="
echo "Rolling:  ${ROLLING:-unknown}"
echo "Weekly:   ${WEEKLY:-unknown}"
echo "Monthly:  ${MONTHLY:-unknown}"

# Check if rolling is critical
ROLLING_PCT=$(echo "$ROLLING" | grep -oP 'usagePercent:\K\d+')
if [ -n "$ROLLING_PCT" ] && [ "$ROLLING_PCT" -gt 70 ]; then
    echo ""
    echo "⚠️  WARNING: Rolling usage at ${ROLLING_PCT}%. Consider pausing."
    exit 1
else
    echo "✅ Rolling usage OK (${ROLLING_PCT:-unknown}%)"
    exit 0
fi
