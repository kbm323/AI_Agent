from __future__ import annotations

from src.runtime_architecture_v2.scheduling_policy import (
    SchedulingKind,
    SchedulingPolicy,
    SchedulingRequest,
)


def test_scheduling_policy_defaults_durable_meeting_work_to_hermes_kanban():
    decision = SchedulingPolicy().decide(
        SchedulingRequest(
            meeting_run_id="mr_001",
            route_type="creative_meeting",
            durable=True,
            long_running=False,
            scheduled=False,
            simulation=False,
        )
    )

    assert decision.kind == SchedulingKind.HERMES_KANBAN
    assert decision.hermes_primitive == "kanban"
    assert decision.reason == "durable task-board style MeetingRun work"
    assert decision.requires_custom_queue_store is False


def test_scheduling_policy_uses_background_for_bounded_long_running_execution():
    decision = SchedulingPolicy().decide(
        SchedulingRequest(
            meeting_run_id="mr_002",
            route_type="technical_execution",
            durable=False,
            long_running=True,
            scheduled=False,
            simulation=False,
        )
    )

    assert decision.kind == SchedulingKind.HERMES_BACKGROUND_PROCESS
    assert decision.hermes_primitive == "background_process"
    assert decision.requires_custom_queue_store is False


def test_scheduling_policy_uses_cron_for_scheduled_or_retryable_jobs():
    decision = SchedulingPolicy().decide(
        SchedulingRequest(
            meeting_run_id="mr_003",
            route_type="validation_retry",
            durable=False,
            long_running=False,
            scheduled=True,
            retryable=True,
            simulation=False,
        )
    )

    assert decision.kind == SchedulingKind.HERMES_CRON
    assert decision.hermes_primitive == "cron"
    assert decision.requires_custom_queue_store is False


def test_scheduling_policy_allows_local_fake_only_for_tests_and_simulation():
    decision = SchedulingPolicy().decide(
        SchedulingRequest(
            meeting_run_id="mr_sim",
            route_type="mixed_request",
            durable=True,
            simulation=True,
        )
    )

    assert decision.kind == SchedulingKind.LOCAL_FAKE
    assert decision.hermes_primitive == "local_fake"
    assert decision.requires_custom_queue_store is False
    assert "queue.db" not in decision.to_dict()
