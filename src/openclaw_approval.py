"""OpenClaw human-in-the-loop approval policy for AC17."""

from __future__ import annotations

from dataclasses import dataclass

_HIGH_RISK_LEVELS = {"high", "critical"}


@dataclass(frozen=True)
class OpenClawAction:
    """Action descriptor evaluated before OpenClaw execution."""

    execution_id: str
    action_type: str
    risk_level: str
    target: str
    approved_by: str | None = None

    def __post_init__(self) -> None:
        if not self.execution_id:
            raise ValueError("execution_id must be non-empty")
        if not self.action_type:
            raise ValueError("action_type must be non-empty")
        if not self.target:
            raise ValueError("target must be non-empty")
        object.__setattr__(self, "risk_level", self.risk_level.lower().strip())


@dataclass(frozen=True)
class ApprovalDecision:
    """Pre-execution approval gate result."""

    execution_id: str
    requires_approval: bool
    allowed_to_execute: bool
    state: str
    approved_by: str | None = None


def evaluate_hitl_approval(action: OpenClawAction) -> ApprovalDecision:
    """Block high-risk OpenClaw actions until human approval exists."""
    requires_approval = action.risk_level in _HIGH_RISK_LEVELS
    if not requires_approval:
        return ApprovalDecision(
            execution_id=action.execution_id,
            requires_approval=False,
            allowed_to_execute=True,
            state="auto_approved",
        )
    if action.approved_by:
        return ApprovalDecision(
            execution_id=action.execution_id,
            requires_approval=True,
            allowed_to_execute=True,
            state="approved",
            approved_by=action.approved_by,
        )
    return ApprovalDecision(
        execution_id=action.execution_id,
        requires_approval=True,
        allowed_to_execute=False,
        state="awaiting_human_approval",
    )
