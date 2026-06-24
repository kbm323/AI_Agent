#!/usr/bin/env python3
"""Run the Phase 18 Live Kanban Autonomous Dispatch pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.dispatch_loop import (  # noqa: E402
    run_phase18_autonomous_dispatch,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 18 Live Kanban Autonomous Dispatch",
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "live"),
        default="dry-run",
        help="Dispatch mode (default: dry-run)",
    )
    parser.add_argument(
        "--meeting-run-id",
        default="",
        help="MeetingRun ID (auto-generated if omitted)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="Maximum dispatch loop rounds (default: 3)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = run_phase18_autonomous_dispatch(
        root=REPO_ROOT,
        mode=args.mode,
        meeting_run_id=args.meeting_run_id,
        max_rounds=args.max_rounds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
