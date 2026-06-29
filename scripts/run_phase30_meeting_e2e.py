#!/usr/bin/env python3
"""Run Phase 30 GPT-only MeetingRun E2E dry-run.

This CLI never calls opencode-go. It uses injected deterministic role outputs and
an in-memory thread projection adapter by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.meeting_e2e import (  # noqa: E402
    FakeMeetingThreadProjectionAdapter,
    InjectedRoleOutputProvider,
    OpenCodeGoRoleOutputProvider,
    run_phase30_meeting_e2e,
)
from src.runtime_architecture_v2.schemas import MeetingRunState  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 30 meeting E2E dry-run.")
    parser.add_argument("--root", default=".", help="Repository/project root")
    parser.add_argument(
        "--trigger-text",
        default="Phase 30 GPT-only 7봇 회의 dry-run",
        help="Meeting trigger text",
    )
    parser.add_argument("--user-id", default="phase30-cli-user")
    parser.add_argument("--channel-id", default="phase30-cli-channel")
    parser.add_argument("--guild-id", default="")
    parser.add_argument(
        "--use-opencode-go",
        action="store_true",
        help="Use OpenCode Go role provider instead of deterministic dry-run provider.",
    )
    parser.add_argument("--opencode-model", default="glm-5.2")
    parser.add_argument("--opencode-timeout-sec", type=int, default=120)
    parser.add_argument(
        "--opencode-runner-fixture-output",
        default="",
        help="Test-only fixture output. Avoids live opencode-go process execution.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    role_output_provider = InjectedRoleOutputProvider()
    if args.use_opencode_go:
        opencode_runner = None
        if args.opencode_runner_fixture_output:

            def fixture_runner(argv, *, input_text, timeout_sec, env):
                del argv, input_text, timeout_sec, env
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {"content": args.opencode_runner_fixture_output},
                        ensure_ascii=False,
                    ),
                    "stderr": "",
                    "duration_sec": 0.0,
                    "timed_out": False,
                }

            opencode_runner = fixture_runner

        role_output_provider = OpenCodeGoRoleOutputProvider(
            runner=opencode_runner,
            model=args.opencode_model,
            timeout_sec=args.opencode_timeout_sec,
        )
    result = run_phase30_meeting_e2e(
        root=args.root,
        trigger_text=args.trigger_text,
        user_id=args.user_id,
        channel_id=args.channel_id,
        guild_id=args.guild_id,
        role_output_provider=role_output_provider,
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
    )
    state = cast(MeetingRunState, result.meeting_run.state)
    payload = {
        "ok": result.ok,
        "mode": "opencode-go" if args.use_opencode_go else "dry-run",
        "opencode_used": result.validation_packet.opencode_used,
        "opencode_result_count": len(
            getattr(role_output_provider, "last_results", [])
        ),
        "meeting_run_id": result.meeting_run_id,
        "thread_id": result.projection.thread_id,
        "posted_count": result.projection.posted_count,
        "state": str(state.value),
        "final_report_path": result.final_report_path,
        "evidence_path": result.evidence_path,
        "error": result.error,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
