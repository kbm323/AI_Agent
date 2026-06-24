#!/bin/bash
# Pre-commit secret scan — Phase 23 hardening
# Blocks commits that introduce secret-like assignment patterns.
# GitHub secret scanning + push protection are also enabled server-side.

set -euo pipefail

RED='\033[0;31m'
NC='\033[0m'

# Patterns that look like real secret assignments (not test fixtures like token=sk-secret)
PATTERNS=(
    'discord_bot_token\s*=\s*[A-Za-z0-9._-]{20,}'
    'DISCORD_BOT_TOKEN\s*=\s*[A-Za-z0-9._-]{20,}'
    'authorization:\s*bearer\s+[A-Za-z0-9._-]{20,}'
    'api[_-]?key\s*=\s*[A-Za-z0-9]{16,}'
    'API[_-]?KEY\s*=\s*[A-Za-z0-9]{16,}'
    'OPENAI_API_KEY\s*=\s*sk-'
    'ANTHROPIC_API_KEY\s*=\s*sk-'
    'GLM_API_KEY\s*=\s*[A-Za-z0-9]{16,}'
    'OPENCODE_GO_API_KEY\s*=\s*[A-Za-z0-9]{16,}'
)

# Get changed files in this commit
CHANGED=$(git diff --cached --name-only --diff-filter=ACM)

if [ -z "$CHANGED" ]; then
    exit 0
fi

FOUND=0
while IFS= read -r file; do
    # Skip binary files, .gitignore targets
    if [ ! -f "$file" ]; then continue; fi
    if echo "$file" | grep -qE '\.(png|jpg|jpeg|gif|svg|ico|woff|ttf|eot|mp3|mp4|wav|zip|tar|gz)$'; then
        continue
    fi
    if echo "$file" | grep -qE '^(runtime/|\.git/)'; then
        continue
    fi

    for pattern in "${PATTERNS[@]}"; do
        if git show ":$file" | grep -qP "$pattern" 2>/dev/null; then
            echo -e "${RED}[SECRET SCAN BLOCKED]${NC} $file: matches pattern '$pattern'"
            FOUND=1
        fi
    done
done <<< "$CHANGED"

if [ "$FOUND" -eq 1 ]; then
    echo ""
    echo -e "${RED}Commit blocked: secret-like assignment detected.${NC}"
    echo "If this is a false positive (test fixture), adjust the scan pattern."
    echo "Never commit real tokens, API keys, or bot credentials."
    exit 1
fi

exit 0
