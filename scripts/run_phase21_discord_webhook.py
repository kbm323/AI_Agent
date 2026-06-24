#!/usr/bin/env python3
"""Phase 21 Discord Interaction Webhook — generate slash command manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.discord_webhook import (  # noqa: E402
    run_phase21_webhook,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 21 Discord Interaction Webhook",
    )
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    args = parser.parse_args()
    result = run_phase21_webhook(root=REPO_ROOT, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
