"""OpenClaw intervention policy for AC18.

Live OpenClaw executions are cancel-only.  A semantic retune is modelled
as cancelling the current execution and starting a new execution_id.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InterventionResult:
    """Result of applying an operator intervention."""

    cancelled_execution_id: str | None
    new_execution_id: str | None
    state: str
    reason: str
    error: str | None = None


def apply_intervention(
    *,
    execution_id: str,
    intervention_type: str,
    reason: str,
    new_execution_id: str | None = None,
) -> InterventionResult:
    """Apply cancel-only intervention semantics."""
    if not execution_id:
        raise ValueError("execution_id must be non-empty")
    if not reason:
        raise ValueError("reason must be non-empty")

    normalized = intervention_type.lower().strip()
    if normalized == "cancel":
        return InterventionResult(
            cancelled_execution_id=execution_id,
            new_execution_id=None,
            state="cancelled",
            reason=reason,
        )

    if normalized == "semantic_retune":
        if not new_execution_id:
            raise ValueError("new_execution_id is required for semantic_retune")
        if new_execution_id == execution_id:
            raise ValueError("new_execution_id must differ from execution_id")
        return InterventionResult(
            cancelled_execution_id=execution_id,
            new_execution_id=new_execution_id,
            state="retuned",
            reason=reason,
        )

    return InterventionResult(
        cancelled_execution_id=None,
        new_execution_id=None,
        state="rejected",
        reason=reason,
        error="only cancel or semantic_retune interventions are allowed",
    )
