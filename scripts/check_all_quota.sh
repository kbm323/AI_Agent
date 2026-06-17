#!/bin/bash
# === AI Company Quota Checker (Hierarchical) ===
# Priority: Monthly > Weekly > Hourly
# If higher tier exhausted, lower tier availability doesn't matter

echo "=== $(date '+%H:%M:%S') ==="

# --- OpenCode Go ---
AUTH_COOKIE="oc_locale=ko; auth=Fe26.2**f38839143e0e2b89396ddeb844b99068f18dbc6d69cebb2b0262f9f2baafb24d*lN9mRl-FRkjwQVe5hlZkfA*ymb4zz4T_aurOQDxi3nsyOzpfGsWBQ9TN5cTEQyyrdT3tiaSx24rlV6yQaqmzWGJN8xo8-ZnlT9dcd8on6nGa5s97R3gKk8REtSSbWmzR4UwPL1NlKq7DJo-r2TprvPxrYcVJI-1CNj7d_13uGuHxFQ08adJILhrNnX3ZKAeuHxLcbfD--ad6diZLCZeeRC9RVFfO1b-lsTU9QW72cu_tbap1tS5gpMzUZhXvmjv7O8ZHpr6wDICf45_mH4gUVKBDfzBoLoB_yusXc-XON5-Z33p6_PsCQsYIOyEZBm1M8cTWPJntrFqcTHtbIJ6gCvo*1811006952108*157d4a5b3dac1c4e97c3e859082cecd7eb2e3b65bc141aff932871c67a8385f3*yGlrYQ2EN4OY3cp7x9MGyKqzcCTvCjSlBZtreAxKdcM"
WORKSPACE_ID="wrk_01KS8BQQKFNR9SSS98DDE1JZEX"

RESPONSE=$(curl -s "https://opencode.ai/workspace/${WORKSPACE_ID}/go"   -H 'accept: text/html' -H 'user-agent: Mozilla/5.0'   -b "$AUTH_COOKIE" 2>/dev/null)

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
sec_to_hms() { printf "%dh %dm" $(($1/3600)) $(($1%3600/60)); }

check_provider() {
    local name=$1 monthly=$2 weekly=$3 hourly=$4 m_reset=$5 w_reset=$6 h_reset=$7
    local status="✅"
    local reason=""
    
    if [ "$monthly" -ge 100 ] 2>/dev/null; then
        status="🔴 BLOCKED"
        reason="Monthly ${monthly}% (reset: $(sec_to_hms $m_reset))"
    elif [ "$weekly" -ge 100 ] 2>/dev/null; then
        status="🔴 BLOCKED"
        reason="Weekly ${weekly}% (reset: $(sec_to_hms $w_reset))"
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
check_provider "📦 Go" "$GO_MONTHLY" "$GO_WEEKLY" "$GO_ROLLING" "$GO_MONTHLY_RESET" "$GO_WEEKLY_RESET" "$GO_ROLLING_RESET"
check_provider "🤖 Codex" "${CX_MONTHLY:-0}" "$CX_WEEKLY" "$CX_HOURLY" "0" "0" "$CX_HOURLY_RESET"

# --- Work Decision ---
GO_OK=false; CX_OK=false
[ "$GO_MONTHLY" -lt 100 ] 2>/dev/null && [ "$GO_WEEKLY" -lt 100 ] 2>/dev/null && [ "$GO_ROLLING" -lt 100 ] 2>/dev/null && GO_OK=true
[ "${CX_MONTHLY:-0}" -lt 100 ] 2>/dev/null && [ "$CX_WEEKLY" -lt 100 ] 2>/dev/null && [ "$CX_HOURLY" -lt 100 ] 2>/dev/null && CX_OK=true

echo ""
if $GO_OK && $CX_OK; then echo "✅ Both available"; 
elif $GO_OK; then echo "⚠️  Only OpenCode Go available - use Go for work";
elif $CX_OK; then echo "⚠️  Only Codex available - use Codex for work";
else echo "🔴 All blocked - wait for reset"; fi
