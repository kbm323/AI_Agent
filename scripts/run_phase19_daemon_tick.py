#!/usr/bin/env python3
"""Run one Phase 19 Autonomous Scheduling Daemon tick."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.daemon import (  # noqa: E402
    run_phase19_daemon_tick,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 19 Autonomous Scheduling Daemon",
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "live"),
        default="dry-run",
        help="Daemon mode (default: dry-run)",
    )
    parser.add_argument(
        "--max-stuck-threshold",
        type=int,
        default=3,
        help="Max stuck runs before health gate blocks new meetings (default: 3)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = run_phase19_daemon_tick(
        root=REPO_ROOT,
        mode=args.mode,
        max_stuck_threshold=args.max_stuck_threshold,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
