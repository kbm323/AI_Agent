"""Pure convergence decisions for bounded Runtime v2 meetings."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from .schemas import MeetingOutcome, MeetingOutcomeStatus


@dataclass(frozen=True)
class ConvergencePolicy:
    min_rounds: int = 2
    max_rounds: int = 6
    deadlock_threshold: int = 2

    def __post_init__(self) -> None:
        if self.min_rounds < 2:
            raise ValueError("meeting convergence requires at least two rounds")
        if self.max_rounds < self.min_rounds:
            raise ValueError("max_rounds must be at least min_rounds")
        if self.deadlock_threshold < 2:
            raise ValueError("deadlock_threshold must be at least two")


@dataclass(frozen=True)
class ConvergenceDecision:
    action: Literal["stop", "continue", "arbitrate"]
    reason: str
    next_roles: tuple[str, ...] = ()


def disagreement_fingerprint(outcome: MeetingOutcome) -> str:
    """Return a digest for normalized disagreement text, never the raw text."""

    normalized = sorted(
        value
        for item in outcome.disagreements
        if (value := re.sub(r"\s+", " ", item).strip().casefold())
    )
    if not normalized:
        return ""
    payload = "\n".join(normalized).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def decide_convergence(
    outcomes: tuple[MeetingOutcome, ...],
    *,
    participants: tuple[str, ...],
    completed_rounds: int,
    policy: ConvergencePolicy | None = None,
) -> ConvergenceDecision:
    """Choose the next bounded meeting action from durable outcomes."""

    if not outcomes:
        raise ValueError("at least one meeting outcome is required")
    policy = policy or ConvergencePolicy()
    latest = outcomes[-1]

    if latest.generation_status != "live":
        return ConvergenceDecision("stop", "outcome_failed")
    if latest.status == MeetingOutcomeStatus.AGREED:
        return ConvergenceDecision("stop", "agreed")
    if latest.status == MeetingOutcomeStatus.NEEDS_USER_DECISION:
        return ConvergenceDecision("stop", "needs_user_decision")
    if not disagreement_fingerprint(latest):
        return ConvergenceDecision("stop", "unstructured_disagreement")
    if completed_rounds >= policy.max_rounds:
        return ConvergenceDecision("stop", "max_rounds_reached")

    recent = outcomes[-policy.deadlock_threshold :]
    fingerprints = tuple(disagreement_fingerprint(outcome) for outcome in recent)
    if (
        len(fingerprints) == policy.deadlock_threshold
        and fingerprints[0]
        and len(set(fingerprints)) == 1
    ):
        return ConvergenceDecision(
            "arbitrate",
            "repeated_disagreement",
            ("ceo_coordinator",),
        )

    unresolved = set(latest.unresolved_roles)
    if unresolved:
        if "validation_audit" in participants:
            unresolved.add("validation_audit")
        next_roles = tuple(role for role in participants if role in unresolved)
        return ConvergenceDecision("continue", "unresolved_roles", next_roles)

    return ConvergenceDecision(
        "continue",
        "unresolved_roles_missing",
        participants,
    )


__all__ = [
    "ConvergenceDecision",
    "ConvergencePolicy",
    "decide_convergence",
    "disagreement_fingerprint",
]
