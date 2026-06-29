"""Meeting scheduler preemption/resume helpers for AC10.

P0 incidents may preempt lower-priority meetings.  Paused meetings carry
`resume_from_step` from manifest.completed_step so recovery can continue
without replaying completed work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from src.priority_queue import MeetingQueueItem

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class RunningMeeting:
    """Meeting currently occupying a scheduler slot."""

    meeting_id: str
    priority: str
    completed_step: str

    def __post_init__(self) -> None:
        normalized = self.priority.upper()
        if normalized not in _PRIORITY_RANK:
            raise ValueError("priority must be one of P0, P1, P2, P3")
        if not self.meeting_id:
            raise ValueError("meeting_id must be non-empty")
        object.__setattr__(self, "priority", normalized)


@dataclass(frozen=True)
class PausedMeeting:
    """Lower-priority meeting paused to make room for P0."""

    meeting_id: str
    priority: str
    resume_from_step: str
    state: str = "paused"


@dataclass(frozen=True)
class PreemptionDecision:
    """Scheduler decision for an incoming P0 meeting."""

    started: MeetingQueueItem
    paused: PausedMeeting | None
    active_meeting_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResumedMeeting:
    """Resume descriptor reconstructed from a paused manifest."""

    meeting_id: str
    priority: str
    state: str
    resume_from_step: str
    outputs_preserved: tuple[str, ...]


def schedule_p0_preemption(
    running: Sequence[RunningMeeting],
    incoming: MeetingQueueItem,
    *,
    max_concurrent: int = 2,
) -> PreemptionDecision:
    """Start a P0 meeting, pausing the lowest-priority running meeting if needed."""
    if incoming.priority != "P0":
        raise ValueError("schedule_p0_preemption only accepts incoming P0 meetings")
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be >= 1")

    active = list(running)
    paused: PausedMeeting | None = None

    if len(active) >= max_concurrent:
        preemptable = [m for m in active if _PRIORITY_RANK[m.priority] > _PRIORITY_RANK[incoming.priority]]
        if not preemptable:
            raise ValueError("no lower-priority running meeting can be preempted")
        victim = max(preemptable, key=lambda m: (_PRIORITY_RANK[m.priority], m.meeting_id))
        active = [m for m in active if m.meeting_id != victim.meeting_id]
        paused = PausedMeeting(
            meeting_id=victim.meeting_id,
            priority=victim.priority,
            resume_from_step=victim.completed_step,
        )

    active_ids = tuple(m.meeting_id for m in active) + (incoming.meeting_id,)
    return PreemptionDecision(
        started=incoming,
        paused=paused,
        active_meeting_ids=active_ids,
    )


def resume_paused_meeting(manifest: Mapping[str, object]) -> ResumedMeeting:
    """Create a resume descriptor from manifest.completed_step."""
    meeting_id = str(manifest.get("meeting_id") or "")
    priority = str(manifest.get("priority") or "")
    completed_step = str(manifest.get("completed_step") or "")
    outputs_raw = manifest.get("outputs", ())
    if isinstance(outputs_raw, (list, tuple)):
        outputs = tuple(str(item) for item in outputs_raw)
    else:
        outputs = (str(outputs_raw),) if outputs_raw else ()

    if not meeting_id:
        raise ValueError("manifest.meeting_id is required")
    if priority.upper() not in _PRIORITY_RANK:
        raise ValueError("manifest.priority must be one of P0, P1, P2, P3")
    if not completed_step:
        raise ValueError("manifest.completed_step is required for resume")

    return ResumedMeeting(
        meeting_id=meeting_id,
        priority=priority.upper(),
        state="resuming",
        resume_from_step=completed_step,
        outputs_preserved=outputs,
    )
