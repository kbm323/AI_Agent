#!/usr/bin/env python3
"""Run the Phase 16 autonomous scheduling / Kanban operations pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.kanban_ops import (  # noqa: E402
    run_phase16_kanban_pilot,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Phase 16 Hermes-native Kanban operations pilot."
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run",),
        default="dry-run",
        help="Only deterministic dry-run is supported in Phase 16.",
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository/root directory where runtime artifacts are written.",
    )
    parser.add_argument(
        "--knowledge-query",
        default="버추얼 아이돌 팬 참여 쇼츠 데뷔",
        help="Deterministic query used to retrieve prior Phase 15 knowledge context.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase16_kanban_pilot(
        root=Path(args.root),
        mode=args.mode,
        knowledge_query=args.knowledge_query,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
