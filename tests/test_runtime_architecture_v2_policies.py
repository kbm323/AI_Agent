from __future__ import annotations

from src.runtime_architecture_v2.policies import (
    ObservabilityPolicy,
    QuotaPolicy,
    QuotaSnapshot,
    SecurityPolicy,
)
from src.runtime_architecture_v2.schemas import MeetingRun


def test_security_policy_blocks_secret_like_trigger_and_returns_redacted_reason():
    run = MeetingRun.create(
        meeting_run_id="mr_security_policy",
        trigger_text="회의 전에 API_TOKEN=example-secret-value 값을 확인해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )

    decision = SecurityPolicy().evaluate(run)

    assert decision.allowed is False
    assert decision.reason == "secret_like_input_detected"
    assert "example-secret-value" not in decision.safe_summary
    assert "[REDACTED]" in decision.safe_summary
    assert decision.next_state == "paused"


def test_quota_policy_pauses_when_active_provider_weekly_quota_is_critical():
    snapshot = QuotaSnapshot(
        provider="codex",
        monthly_percent=0,
        weekly_percent=99,
        hourly_percent=12,
    )

    decision = QuotaPolicy(snapshot=snapshot).evaluate(active_provider="codex")

    assert decision.allowed is False
    assert decision.reason == "quota_weekly_critical"
    assert decision.next_state == "paused"
    assert "weekly 99%" in decision.safe_summary


def test_observability_policy_emits_redacted_structured_events_without_raw_trigger():
    run = MeetingRun.create(
        meeting_run_id="mr_observe",
        trigger_text="API_TOKEN=example-secret-value 포함 회의",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )

    event = ObservabilityPolicy().event(
        run,
        stage="security_gate",
        outcome="blocked",
        severity="warning",
        detail="API_TOKEN=example-secret-value",
    )

    assert event["meeting_run_id"] == "mr_observe"
    assert event["stage"] == "security_gate"
    assert event["outcome"] == "blocked"
    assert event["severity"] == "warning"
    assert event["event"] == "observability_event"
    assert "example-secret-value" not in str(event)
    assert "[REDACTED]" in event["detail"]
