#!/bin/bash
# opencode-go quota checker
# Usage: ./check_quota.sh

AUTH_COOKIE="oc_locale=ko; auth=Fe26.2**f38839143e0e2b89396ddeb844b99068f18dbc6d69cebb2b0262f9f2baafb24d*lN9mRl-FRkjwQVe5hlZkfA*ymb4zz4T_aurOQDxi3nsyOzpfGsWBQ9TN5cTEQyyrdT3tiaSx24rlV6yQaqmzWGJN8xo8-ZnlT9dcd8on6nGa5s97R3gKk8REtSSbWmzR4UwPL1NlKq7DJo-r2TprvPxrYcVJI-1CNj7d_13uGuHxFQ08adJILhrNnX3ZKAeuHxLcbfD--ad6diZLCZeeRC9RVFfO1b-lsTU9QW72cu_tbap1tS5gpMzUZhXvmjv7O8ZHpr6wDICf45_mH4gUVKBDfzBoLoB_yusXc-XON5-Z33p6_PsCQsYIOyEZBm1M8cTWPJntrFqcTHtbIJ6gCvo*1811006952108*157d4a5b3dac1c4e97c3e859082cecd7eb2e3b65bc141aff932871c67a8385f3*yGlrYQ2EN4OY3cp7x9MGyKqzcCTvCjSlBZtreAxKdcM"
WORKSPACE_ID="wrk_01KS8BQQKFNR9SSS98DDE1JZEX"

RESPONSE=$(curl -s "https://opencode.ai/workspace/${WORKSPACE_ID}/go"   -H 'accept: text/html'   -H 'user-agent: Mozilla/5.0'   -b "$AUTH_COOKIE" 2>/dev/null)

# Extract usage data from embedded JS state
ROLLING=$(echo "$RESPONSE" | grep -oP 'rollingUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1)
WEEKLY=$(echo "$RESPONSE" | grep -oP 'weeklyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1)
MONTHLY=$(echo "$RESPONSE" | grep -oP 'monthlyUsage:\$R\[\d+\]=\{status:\K[^}]+' | head -1)

echo "=== OpenCode Go Usage ==="
echo "Rolling:  $ROLLING"
echo "Weekly:   $WEEKLY"
echo "Monthly:  $MONTHLY"

# Check if rolling is critical
ROLLING_PCT=$(echo "$ROLLING" | grep -oP 'usagePercent:\K\d+')
if [ -n "$ROLLING_PCT" ] && [ "$ROLLING_PCT" -gt 70 ]; then
    echo ""
    echo "⚠️  WARNING: Rolling usage at ${ROLLING_PCT}%. Consider pausing."
    exit 1
else
    echo "✅ Rolling usage OK (${ROLLING_PCT}%)"
    exit 0
fi
