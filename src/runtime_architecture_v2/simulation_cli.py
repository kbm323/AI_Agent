"""Deterministic end-to-end simulation CLI for Runtime Architecture v2.

This Phase 9 CLI drives RuntimeOrchestrator with fake/injected boundaries only.
It does not call Discord, opencode-go, provider dashboards, or Hermes runtime APIs.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .orchestrator import RuntimeOrchestrator, RuntimeOrchestratorResult
from .policies import QuotaPolicy, QuotaSnapshot
from .schemas import MeetingRunState, ValidationVerdictValue
from .workers import FakeWorkerRunner


def main(argv: Sequence[str] | None = None) -> int:
    """Run one deterministic fake/simulation MeetingRun and print JSON report."""

    args = _parse_args(argv)
    quota_policy = _quota_policy_from_args(args)
    orchestrator = RuntimeOrchestrator(
        root=args.root,
        worker_runner=FakeWorkerRunner(),
        quota_policy=quota_policy,
        active_provider=args.active_provider,
    )
    result = orchestrator.run(
        meeting_run_id=args.meeting_run_id,
        trigger_text=args.trigger_text,
        user_id=args.user_id,
        channel_id=args.channel_id,
        thread_id=args.thread_id,
        guild_id=args.guild_id,
        hermes_session_id=args.hermes_session_id,
        priority=args.priority,
        simulation=_parse_bool(args.simulation),
    )
    report = result_to_report(result)
    _print_json(report)
    return 0 if report["ok"] else 2


def result_to_report(result: RuntimeOrchestratorResult) -> dict[str, Any]:
    """Convert RuntimeOrchestratorResult into a stable CLI JSON report."""

    verdicts = tuple(str(verdict.verdict) for verdict in result.validation_verdicts)
    return {
        "ok": result.meeting_run.state == MeetingRunState.COMPLETED,
        "mode": "simulation",
        "used_live_adapters": False,
        "meeting_run_id": result.meeting_run.meeting_run_id,
        "state": str(result.meeting_run.state),
        "route_type": result.routing_result.route_type,
        "scheduling_kind": str(result.scheduling_decision.kind),
        "scheduling_primitive": result.scheduling_decision.hermes_primitive,
        "requires_custom_queue_store": (
            result.scheduling_decision.requires_custom_queue_store
        ),
        "worker_task_count": len(result.worker_tasks),
        "validation_verdicts": [_enum_text(verdict) for verdict in verdicts],
        "validation_decision": str(result.validation_decision.kind),
        "projection_event_id": result.projection_event.event_id,
        "projection_status": result.projection_publish_result.status,
        "checkpoint_id": result.checkpoint.checkpoint_id,
        "security_reason": result.security_decision.reason,
        "quota_reason": result.quota_decision.reason,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Runtime Architecture v2 deterministic simulation CLI.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--meeting-run-id", required=True)
    parser.add_argument("--trigger-text", required=True)
    parser.add_argument("--user-id", default="simulation-user")
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--guild-id", default="")
    parser.add_argument("--hermes-session-id", default="")
    parser.add_argument("--priority", default="P2")
    parser.add_argument("--simulation", default="true", choices=("true", "false"))
    parser.add_argument("--active-provider", default="opencode-go")
    parser.add_argument("--quota-provider", default="")
    parser.add_argument("--quota-monthly-percent", type=int, default=0)
    parser.add_argument("--quota-weekly-percent", type=int, default=0)
    parser.add_argument("--quota-hourly-percent", type=int, default=0)
    return parser.parse_args(argv)


def _quota_policy_from_args(args: argparse.Namespace) -> QuotaPolicy:
    if not args.quota_provider:
        return QuotaPolicy()
    return QuotaPolicy(
        snapshot=QuotaSnapshot(
            provider=args.quota_provider,
            monthly_percent=args.quota_monthly_percent,
            weekly_percent=args.quota_weekly_percent,
            hourly_percent=args.quota_hourly_percent,
        )
    )


def _parse_bool(value: str) -> bool:
    return value == "true"


def _enum_text(value: str) -> str:
    prefix = f"{ValidationVerdictValue.__name__}."
    return value.removeprefix(prefix)


def _print_json(data: Mapping[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main", "result_to_report"]
