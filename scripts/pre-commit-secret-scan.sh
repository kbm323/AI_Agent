#!/bin/bash
# Secret scan for staged content or a committed Git tree/range.

set -euo pipefail

RED='\033[0;31m'
NC='\033[0m'

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

usage() {
    echo "usage: $0 [--staged | --tree <commit> | --range <base..head>]" >&2
    exit 2
}

MODE=staged
CONTENT_REF=
if [ "$#" -gt 0 ]; then
    MODE="$1"
    shift
fi

case "$MODE" in
    staged|--staged)
        [ "$#" -eq 0 ] || usage
        MODE=staged
        CHANGED="$(git diff --cached --name-only --diff-filter=ACM)"
        ;;
    --tree)
        [ "$#" -eq 1 ] || usage
        CONTENT_REF="$1"
        git rev-parse --verify --quiet "$CONTENT_REF^{commit}" >/dev/null
        CHANGED="$(git ls-tree -r --name-only "$CONTENT_REF")"
        [ -n "$CHANGED" ] || {
            echo "Committed-tree scan must be non-vacuous." >&2
            exit 2
        }
        ;;
    --range)
        [ "$#" -eq 1 ] || usage
        RANGE="$1"
        case "$RANGE" in
            *..*) CONTENT_REF="${RANGE##*..}" ;;
            *) usage ;;
        esac
        git rev-parse --verify --quiet "$CONTENT_REF^{commit}" >/dev/null
        [ "$(git rev-list --count "$RANGE")" -gt 0 ] || {
            echo "Committed range scan must be non-vacuous." >&2
            exit 2
        }
        CHANGED="$(git diff --name-only --diff-filter=ACM "$RANGE")"
        [ -n "$CHANGED" ] || {
            echo "Committed range scan must include changed files." >&2
            exit 2
        }
        ;;
    *) usage ;;
esac

if [ -z "$CHANGED" ]; then
    exit 0
fi

FOUND=0
SCANNED=0
while IFS= read -r file; do
    case "$file" in
        *.png|*.jpg|*.jpeg|*.gif|*.svg|*.ico|*.woff|*.ttf|*.eot|*.mp3|*.mp4|*.wav|*.zip|*.tar|*.gz) continue ;;
        runtime/*|.git/*) continue ;;
    esac

    if [ "$MODE" = staged ]; then
        git cat-file -e ":$file" 2>/dev/null || continue
    else
        git cat-file -e "$CONTENT_REF:$file" 2>/dev/null || continue
    fi
    SCANNED=$((SCANNED + 1))

    for pattern in "${PATTERNS[@]}"; do
        if [ "$MODE" = staged ]; then
            matches=(git grep -qP --cached "$pattern" -- "$file")
        else
            matches=(git grep -qP "$pattern" "$CONTENT_REF" -- "$file")
        fi
        if "${matches[@]}" 2>/dev/null; then
            echo -e "${RED}[SECRET SCAN BLOCKED]${NC} $file: matches a credential pattern"
            FOUND=1
        fi
    done
done <<< "$CHANGED"

if [ "$MODE" != staged ] && [ "$SCANNED" -eq 0 ]; then
    echo "Committed scan must inspect at least one non-runtime text file." >&2
    exit 2
fi

if [ "$FOUND" -eq 1 ]; then
    echo ""
    echo -e "${RED}Commit blocked: secret-like assignment detected.${NC}"
    echo "Never commit real tokens, API keys, or bot credentials."
    exit 1
fi

echo "Secret scan passed: $SCANNED file(s) inspected in $MODE mode."
