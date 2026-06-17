"""Quorum reassessment and role completion policy for AC12/AC13.

This pure module decides the next Coordinator action after worker/role
completion attempts.  It does not execute retries or fallbacks itself;
it returns a deterministic decision consumed by the orchestration layer.
"""

from __future__ import annotations

from dataclasses import dataclass

_VALID_STATUSES = {"completed", "failed", "pending", "running", "skipped"}


@dataclass(frozen=True)
class RoleResult:
    """Completion state for a required or optional meeting role."""

    role_id: str
    required: bool
    status: str
    attempts: int = 0
    max_attempts: int = 1
    fallback_role_id: str | None = None

    def __post_init__(self) -> None:
        if not self.role_id or not self.role_id.strip():
            raise ValueError("role_id must be non-empty")
        normalized = self.status.lower().strip()
        if normalized not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        if self.attempts < 0:
            raise ValueError("attempts must be >= 0")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        object.__setattr__(self, "status", normalized)

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def can_retry(self) -> bool:
        return self.failed and self.attempts < self.max_attempts

    @property
    def retry_exhausted(self) -> bool:
        return self.failed and self.attempts >= self.max_attempts


@dataclass(frozen=True)
class QuorumAssessment:
    """Coordinator action after reassessing role completion."""

    decision: str
    quorum_met: bool
    completed_required_count: int
    required_quorum: int
    next_role_id: str | None = None
    degraded: bool = False
    skipped_optional_roles: tuple[str, ...] = ()
    escalation_required: bool = False


def reassess_quorum(
    *,
    roles: tuple[RoleResult, ...],
    required_quorum: int,
) -> QuorumAssessment:
    """Apply retry/fallback/quorum/escalation policy to role results."""
    if required_quorum < 1:
        raise ValueError("required_quorum must be >= 1")
    if not roles:
        raise ValueError("roles must not be empty")

    required_roles = tuple(role for role in roles if role.required)
    completed_required = tuple(role for role in required_roles if role.completed)
    completed_count = len(completed_required)
    quorum_met = completed_count >= required_quorum

    for role in required_roles:
        if role.can_retry:
            return QuorumAssessment(
                decision="retry_required_role",
                quorum_met=False,
                completed_required_count=completed_count,
                required_quorum=required_quorum,
                next_role_id=role.role_id,
            )

    for role in required_roles:
        if role.retry_exhausted and role.fallback_role_id:
            return QuorumAssessment(
                decision="fallback_required_role",
                quorum_met=False,
                completed_required_count=completed_count,
                required_quorum=required_quorum,
                next_role_id=role.fallback_role_id,
            )

    failed_required = tuple(role for role in required_roles if role.retry_exhausted)
    if failed_required and not quorum_met:
        return QuorumAssessment(
            decision="escalate_or_fail",
            quorum_met=False,
            completed_required_count=completed_count,
            required_quorum=required_quorum,
            escalation_required=True,
        )

    skipped_optional = tuple(
        role.role_id
        for role in roles
        if not role.required and role.failed and role.retry_exhausted
    )
    if skipped_optional and quorum_met:
        return QuorumAssessment(
            decision="skip_optional_degraded",
            quorum_met=True,
            completed_required_count=completed_count,
            required_quorum=required_quorum,
            degraded=True,
            skipped_optional_roles=skipped_optional,
        )

    if quorum_met:
        return QuorumAssessment(
            decision="quorum_met",
            quorum_met=True,
            completed_required_count=completed_count,
            required_quorum=required_quorum,
        )

    return QuorumAssessment(
        decision="awaiting_roles",
        quorum_met=False,
        completed_required_count=completed_count,
        required_quorum=required_quorum,
    )
