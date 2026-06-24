#!/usr/bin/env python3
"""Generate the Phase 20 29-bot deployment manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.bot_registry import (  # noqa: E402
    run_phase20_bot_registry,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 20 29-Bot Discord Registry",
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "live"),
        default="dry-run",
        help="Mode (default: dry-run)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = run_phase20_bot_registry(root=REPO_ROOT, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
