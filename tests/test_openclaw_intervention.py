"""Tests for OpenClaw cancel-only intervention (AC18)."""

from __future__ import annotations

from src.openclaw_intervention import apply_intervention


def test_cancel_intervention_cancels_existing_execution() -> None:
    result = apply_intervention(
        execution_id="exec-1",
        intervention_type="cancel",
        reason="operator stopped risky action",
    )

    assert result.cancelled_execution_id == "exec-1"
    assert result.new_execution_id is None
    assert result.state == "cancelled"


def test_semantic_retune_is_cancel_plus_new_execution_id() -> None:
    result = apply_intervention(
        execution_id="exec-1",
        intervention_type="semantic_retune",
        reason="change target semantics",
        new_execution_id="exec-2",
    )

    assert result.cancelled_execution_id == "exec-1"
    assert result.new_execution_id == "exec-2"
    assert result.state == "retuned"


def test_direct_parameter_mutation_is_rejected() -> None:
    result = apply_intervention(
        execution_id="exec-1",
        intervention_type="modify_parameters",
        reason="try to mutate active execution",
    )

    assert result.state == "rejected"
    assert result.error == "only cancel or semantic_retune interventions are allowed"
