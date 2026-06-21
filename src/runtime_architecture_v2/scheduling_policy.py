"""Hermes-native scheduling adapter policy for MeetingRun work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SchedulingKind(StrEnum):
    HERMES_KANBAN = "hermes_kanban"
    HERMES_BACKGROUND_PROCESS = "hermes_background_process"
    HERMES_CRON = "hermes_cron"
    LOCAL_FAKE = "local_fake"


@dataclass(frozen=True)
class SchedulingRequest:
    meeting_run_id: str
    route_type: str
    durable: bool = True
    long_running: bool = False
    scheduled: bool = False
    retryable: bool = False
    simulation: bool = False


@dataclass(frozen=True)
class SchedulingDecision:
    meeting_run_id: str
    kind: SchedulingKind
    hermes_primitive: str
    reason: str
    requires_custom_queue_store: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "kind": self.kind.value,
            "hermes_primitive": self.hermes_primitive,
            "reason": self.reason,
            "requires_custom_queue_store": self.requires_custom_queue_store,
        }


class SchedulingPolicy:
    """Map MeetingRun work to existing Hermes primitives before local fakes."""

    def decide(self, request: SchedulingRequest) -> SchedulingDecision:
        if request.simulation:
            return SchedulingDecision(
                meeting_run_id=request.meeting_run_id,
                kind=SchedulingKind.LOCAL_FAKE,
                hermes_primitive="local_fake",
                reason="test or simulation path only",
            )

        if request.scheduled or request.retryable:
            return SchedulingDecision(
                meeting_run_id=request.meeting_run_id,
                kind=SchedulingKind.HERMES_CRON,
                hermes_primitive="cron",
                reason="scheduled or retryable MeetingRun work",
            )

        if request.long_running:
            return SchedulingDecision(
                meeting_run_id=request.meeting_run_id,
                kind=SchedulingKind.HERMES_BACKGROUND_PROCESS,
                hermes_primitive="background_process",
                reason="bounded long-running execution",
            )

        return SchedulingDecision(
            meeting_run_id=request.meeting_run_id,
            kind=SchedulingKind.HERMES_KANBAN,
            hermes_primitive="kanban",
            reason="durable task-board style MeetingRun work",
        )
