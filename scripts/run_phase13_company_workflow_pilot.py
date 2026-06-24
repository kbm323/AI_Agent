#!/usr/bin/env python3
"""Run the Phase 13 live company workflow pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.pilot import (  # noqa: E402
    Phase13PilotModeError,
    run_phase13_pilot,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the bounded Phase 13 company workflow pilot."
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "live-worker"),
        default="dry-run",
        help=(
            "dry-run never calls opencode-go; live-worker permits exactly "
            "one live worker."
        ),
    )
    parser.add_argument(
        "--max-live-workers",
        type=int,
        default=0,
        help="Must be 1 for --mode live-worker; ignored as 0 in dry-run.",
    )
    parser.add_argument(
        "--live-discord",
        action="store_true",
        help=(
            "Actually publish the Discord-safe summary through "
            "LiveDiscordProjectionSink; requires --mode live-worker."
        ),
    )
    parser.add_argument(
        "--target-channel-id",
        default="phase13-channel",
        help="Discord channel ID for projection; defaults to dry-run placeholder.",
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository/root directory where runtime artifacts are written.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_phase13_pilot(
            root=Path(args.root),
            mode=args.mode,
            max_live_workers=args.max_live_workers,
            live_discord=args.live_discord,
            target_channel_id=args.target_channel_id,
        )
    except Phase13PilotModeError as exc:
        print(
            json.dumps(
                {"ok": False, "error": exc.code, "message": exc.message},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(result.to_cli_dict(), ensure_ascii=False, sort_keys=True, indent=2)
    )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
