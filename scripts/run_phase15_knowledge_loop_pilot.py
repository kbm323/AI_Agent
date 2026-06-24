#!/usr/bin/env python3
"""Run the Phase 15 persistent Second Brain knowledge loop pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.knowledge import (  # noqa: E402
    run_phase15_knowledge_loop_pilot,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Phase 15 repo-local knowledge loop pilot."
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run",),
        default="dry-run",
        help="Only deterministic dry-run is supported in Phase 15.",
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository/root directory where knowledge artifacts are written.",
    )
    parser.add_argument(
        "--query",
        default="버추얼 아이돌 팬 참여 쇼츠 데뷔",
        help="Deterministic retrieval query to test the knowledge loop.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase15_knowledge_loop_pilot(
        root=Path(args.root), mode=args.mode, query=args.query
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
