"""Run one durable QMD update/embed reconciliation cycle."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.runtime_architecture_v2.qmd_indexing import QmdIndexScheduler
from src.runtime_architecture_v2.qmd_search import QmdClient


def main(
    argv: Sequence[str] | None = None,
    *,
    client: Any | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Reconcile the Obsidian QMD index.")
    parser.add_argument("--root", required=True, help="AI_Agent workspace root")
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise OSError
    except OSError:
        _print({"error": "invalid_root", "ok": False})
        return 2

    scheduler = QmdIndexScheduler(
        runtime_root=root,
        client=client or QmdClient(),
    )
    result = scheduler.reconcile()
    payload: dict[str, object] = {
        "embedded": result.embedded,
        "ok": result.ok,
        "updated": result.updated,
    }
    if not result.ok:
        payload["error"] = result.error or "command_failed"
    _print(payload)
    return 0 if result.ok else 1


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
