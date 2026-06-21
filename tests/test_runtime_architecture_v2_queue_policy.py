from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.runtime_architecture_v2.queue_policy import (
    ConcurrencyPolicy,
    PriorityInput,
    PriorityQueuePolicy,
)


def test_priority_policy_maps_urgency_and_criticality_to_domain_priority():
    policy = PriorityQueuePolicy(now=lambda: datetime(2026, 1, 1, tzinfo=UTC))

    normal = policy.calculate(
        PriorityInput(
            meeting_run_id="mr_normal", urgency="normal", criticality="normal"
        )
    )
    critical = policy.calculate(
        PriorityInput(
            meeting_run_id="mr_critical", urgency="normal", criticality="critical"
        )
    )
    urgent = policy.calculate(
        PriorityInput(
            meeting_run_id="mr_urgent", urgency="urgent", criticality="normal"
        )
    )

    assert normal.priority == "P2"
    assert critical.priority == "P0"
    assert urgent.priority == "P1"
    assert critical.sort_key < urgent.sort_key < normal.sort_key
    assert normal.metadata["scheduling_primitive_preference"] == "hermes_native"
    assert "queue_db" not in normal.metadata


def test_priority_policy_aging_prevents_starvation():
    now = datetime(2026, 1, 2, tzinfo=UTC)
    policy = PriorityQueuePolicy(now=lambda: now)

    old_normal = policy.calculate(
        PriorityInput(
            meeting_run_id="mr_old",
            urgency="normal",
            criticality="normal",
            created_at=now - timedelta(hours=8),
        )
    )
    fresh_normal = policy.calculate(
        PriorityInput(
            meeting_run_id="mr_fresh",
            urgency="normal",
            criticality="normal",
            created_at=now,
        )
    )

    assert old_normal.priority == "P1"
    assert old_normal.aging_boost > fresh_normal.aging_boost
    assert old_normal.sort_key < fresh_normal.sort_key


def test_priority_policy_accepts_naive_created_at_as_utc():
    policy = PriorityQueuePolicy(now=lambda: datetime(2026, 1, 2, tzinfo=UTC))

    decision = policy.calculate(
        PriorityInput(
            meeting_run_id="mr_naive",
            urgency="normal",
            criticality="normal",
            created_at=datetime(2026, 1, 1, 16, 0, 0),
        )
    )

    assert decision.aging_boost > 0


def test_concurrency_policy_limits_codex_audits_more_than_workers():
    policy = ConcurrencyPolicy(max_worker=4, max_validator=2, max_codex_auditor=1)

    assert policy.limit_for("software_engineer") == 4
    assert policy.limit_for("glm_validator") == 2
    assert policy.limit_for("codex_auditor") == 1
    assert policy.all_limits() == {
        "worker": 4,
        "validator": 2,
        "codex_auditor": 1,
    }


def test_concurrency_policy_rejects_invalid_limits():
    with pytest.raises(ValueError, match="codex auditor concurrency"):
        ConcurrencyPolicy(max_worker=2, max_validator=2, max_codex_auditor=2)

    with pytest.raises(ValueError, match="positive"):
        ConcurrencyPolicy(max_worker=0, max_validator=2, max_codex_auditor=1)
