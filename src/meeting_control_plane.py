"""Pure meeting control-plane wiring.

This module connects scheduling, quorum, OpenClaw approval/intervention,
persona registry, summaries, and follow-up routing into small adapter-facing
functions.  It does not execute external tools or call Discord directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from src.followup_thread_router import FollowupRoute, route_followup
from src.meeting_scheduler import PreemptionDecision, RunningMeeting, schedule_p0_preemption
from src.openclaw_approval import ApprovalDecision, OpenClawAction, evaluate_hitl_approval
from src.openclaw_intervention import InterventionResult, apply_intervention
from src.periodic_summary import PeriodicSummary, generate_periodic_summary
from src.persona_spec_loader import PersonaSpec, load_persona_spec
from src.priority_queue import MeetingQueueItem
from src.quorum_policy import QuorumAssessment, RoleResult, reassess_quorum
from src.team_leader_registry import TeamLeaderRegistry, build_team_leader_registry


@dataclass(frozen=True)
class MeetingRoundControlResult:
    """Combined scheduler and role-quorum decision for one meeting round."""

    preemption: PreemptionDecision
    quorum: QuorumAssessment


@dataclass(frozen=True)
class ExecutionControlResult:
    """OpenClaw pre-execution approval plus optional live intervention result."""

    approval: ApprovalDecision
    intervention: InterventionResult | None = None


@dataclass(frozen=True)
class PersonaBootstrapResult:
    """Loaded persona specs plus the team-leader bot registry."""

    persona_specs: tuple[PersonaSpec, ...]
    registry: TeamLeaderRegistry


def evaluate_meeting_round_controls(
    *,
    running: Sequence[RunningMeeting],
    incoming_meeting_id: str,
    incoming_priority: str,
    roles: tuple[RoleResult, ...],
    required_quorum: int,
    max_concurrent: int = 2,
    created_at: int | float = 0,
) -> MeetingRoundControlResult:
    """Evaluate P0 preemption and role quorum in one control-plane call."""

    incoming = MeetingQueueItem(
        meeting_id=incoming_meeting_id,
        priority=incoming_priority,
        created_at=created_at,
    )
    preemption = schedule_p0_preemption(
        running,
        incoming,
        max_concurrent=max_concurrent,
    )
    quorum = reassess_quorum(roles=roles, required_quorum=required_quorum)
    return MeetingRoundControlResult(preemption=preemption, quorum=quorum)


def evaluate_execution_controls(
    *,
    action: OpenClawAction,
    intervention_type: str | None = None,
    intervention_reason: str | None = None,
    new_execution_id: str | None = None,
) -> ExecutionControlResult:
    """Apply OpenClaw HITL approval and optional cancel-only intervention policy."""

    approval = evaluate_hitl_approval(action)
    intervention: InterventionResult | None = None
    if intervention_type is not None:
        intervention = apply_intervention(
            execution_id=action.execution_id,
            intervention_type=intervention_type,
            reason=intervention_reason or "operator intervention",
            new_execution_id=new_execution_id,
        )
    return ExecutionControlResult(approval=approval, intervention=intervention)


def bootstrap_persona_registry(
    role_dirs: Sequence[str | Path],
    *,
    git_version: str,
) -> PersonaBootstrapResult:
    """Load role persona specs and derive the persistent team-leader registry."""

    specs = tuple(load_persona_spec(path, git_version=git_version) for path in role_dirs)
    roles = tuple(spec.agent_yaml for spec in specs)
    registry = build_team_leader_registry(roles)
    return PersonaBootstrapResult(persona_specs=specs, registry=registry)


def summarize_meeting_period(
    *,
    period: str,
    meetings: Sequence[Mapping[str, object]],
) -> PeriodicSummary:
    """Adapter-facing wrapper for periodic self-reflection summaries."""

    return generate_periodic_summary(period=period, meetings=meetings)


def route_thread_followup(
    *,
    thread_id: str,
    text: str,
    thread_to_meeting: Mapping[str, str],
) -> FollowupRoute:
    """Route Discord follow-up text to an existing or new meeting."""

    return route_followup(thread_id=thread_id, text=text, thread_to_meeting=thread_to_meeting)


__all__ = [
    "ExecutionControlResult",
    "MeetingRoundControlResult",
    "PersonaBootstrapResult",
    "bootstrap_persona_registry",
    "evaluate_execution_controls",
    "evaluate_meeting_round_controls",
    "route_thread_followup",
    "summarize_meeting_period",
]
