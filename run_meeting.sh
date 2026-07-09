#!/bin/bash
# === AI Company Auto-Resume Runner ===
# Runs seed, saves session state, resumes from where left off
# Usage: ./run_meeting.sh [seed_file]

SEED="${1:-seeds/seed_remaining.yaml}"
STATE_FILE=".ouroboros_last_session"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_DIR"

echo "=== AI Company Meeting System Runner ==="
echo "Seed: $SEED"
echo ""

# Check if there's a previous paused session to resume
if [ -f "$STATE_FILE" ]; then
    LAST_SESSION=$(cat "$STATE_FILE")
    echo "Found previous session: $LAST_SESSION"
    echo "Attempting resume..."
    RESULT=$(ouroboros run workflow "$SEED" --runtime hermes --resume "$LAST_SESSION" 2>&1)
    echo "$RESULT"
    
    # If resume fails, start fresh
    if echo "$RESULT" | grep -q "cannot resume\|terminal state"; then
        echo "Resume failed - starting fresh execution"
        rm "$STATE_FILE"
    else
        echo "Resumed session: $LAST_SESSION"
        exit 0
    fi
fi

# Start fresh execution
echo "Starting new execution..."
echo "Provider check:"
echo "  Current model: $(hermes config show 2>/dev/null | grep -A3 '^model:' | grep 'default:' | head -1)"
echo ""

# Run ouroboros (uses hermes runtime, respects current provider)
ouroboros run workflow "$SEED" --runtime hermes 2>&1

# Save exit code
EXIT_CODE=$?

echo ""
echo "Execution finished with code: $EXIT_CODE"

if [ $EXIT_CODE -ne 0 ]; then
    echo "Check quota: bash scripts/check_all_quota.sh"
    echo "To resume: ./run_meeting.sh $SEED"
fi
