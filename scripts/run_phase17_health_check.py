#!/usr/bin/env python3
"""Run the Phase 17 production health check pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.production import (  # noqa: E402
    run_phase17_health_check,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Phase 17 production health check."
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run",),
        default="dry-run",
        help="Only deterministic dry-run is supported in Phase 17.",
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository/root directory to scan for MeetingRun artifacts.",
    )
    parser.add_argument(
        "--stuck-hours",
        type=float,
        default=1.0,
        help="Threshold in hours before a non-terminal run is flagged as stuck.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase17_health_check(
        root=Path(args.root),
        mode=args.mode,
        stuck_hours=args.stuck_hours,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
