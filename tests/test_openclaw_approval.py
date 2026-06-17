"""Tests for OpenClaw HITL approval policy (AC17)."""

from __future__ import annotations

from src.openclaw_approval import OpenClawAction, evaluate_hitl_approval


def test_high_risk_action_requires_human_approval_before_execution() -> None:
    action = OpenClawAction(
        execution_id="exec-1",
        action_type="delete_file",
        risk_level="high",
        target="/prod/data.json",
    )

    decision = evaluate_hitl_approval(action)

    assert decision.requires_approval is True
    assert decision.allowed_to_execute is False
    assert decision.state == "awaiting_human_approval"


def test_approved_high_risk_action_can_execute() -> None:
    action = OpenClawAction(
        execution_id="exec-1",
        action_type="delete_file",
        risk_level="high",
        target="/prod/data.json",
        approved_by="human-operator",
    )

    decision = evaluate_hitl_approval(action)

    assert decision.requires_approval is True
    assert decision.allowed_to_execute is True
    assert decision.state == "approved"


def test_low_risk_action_does_not_require_approval() -> None:
    action = OpenClawAction(
        execution_id="exec-2",
        action_type="read_file",
        risk_level="low",
        target="/tmp/note.txt",
    )

    decision = evaluate_hitl_approval(action)

    assert decision.requires_approval is False
    assert decision.allowed_to_execute is True
    assert decision.state == "auto_approved"
