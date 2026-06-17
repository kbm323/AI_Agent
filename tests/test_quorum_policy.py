"""Tests for quorum reassessment and role completion policy (AC12/AC13)."""

from __future__ import annotations

from src.quorum_policy import RoleResult, reassess_quorum


def test_required_role_failure_triggers_retry_before_fallback() -> None:
    result = reassess_quorum(
        roles=(
            RoleResult(role_id="producer", required=True, status="completed"),
            RoleResult(role_id="security", required=True, status="failed", attempts=1, max_attempts=2),
        ),
        required_quorum=2,
    )

    assert result.decision == "retry_required_role"
    assert result.next_role_id == "security"
    assert result.quorum_met is False


def test_required_role_exhaustion_triggers_fallback() -> None:
    result = reassess_quorum(
        roles=(
            RoleResult(role_id="producer", required=True, status="completed"),
            RoleResult(role_id="security", required=True, status="failed", attempts=2, max_attempts=2, fallback_role_id="tech-lead"),
        ),
        required_quorum=2,
    )

    assert result.decision == "fallback_required_role"
    assert result.next_role_id == "tech-lead"
    assert result.quorum_met is False


def test_optional_role_failure_skips_with_degradation_when_quorum_met() -> None:
    result = reassess_quorum(
        roles=(
            RoleResult(role_id="producer", required=True, status="completed"),
            RoleResult(role_id="director", required=True, status="completed"),
            RoleResult(role_id="trend-analyst", required=False, status="failed", attempts=2, max_attempts=2),
        ),
        required_quorum=2,
    )

    assert result.decision == "skip_optional_degraded"
    assert result.quorum_met is True
    assert result.degraded is True
    assert result.skipped_optional_roles == ("trend-analyst",)


def test_failed_required_role_without_fallback_escalates_when_retries_exhausted() -> None:
    result = reassess_quorum(
        roles=(
            RoleResult(role_id="producer", required=True, status="completed"),
            RoleResult(role_id="legal", required=True, status="failed", attempts=3, max_attempts=3),
        ),
        required_quorum=2,
    )

    assert result.decision == "escalate_or_fail"
    assert result.escalation_required is True
    assert result.quorum_met is False


def test_all_required_roles_complete_passes_quorum() -> None:
    result = reassess_quorum(
        roles=(
            RoleResult(role_id="producer", required=True, status="completed"),
            RoleResult(role_id="director", required=True, status="completed"),
        ),
        required_quorum=2,
    )

    assert result.decision == "quorum_met"
    assert result.quorum_met is True
    assert result.escalation_required is False
