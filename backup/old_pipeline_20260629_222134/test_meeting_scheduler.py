"""Tests for meeting scheduler preemption/resume (AC10).

AC10: P0 meetings may pause lower-priority meetings and lower-priority
meetings resume from manifest.completed_step.
"""

from __future__ import annotations

from src.meeting_scheduler import (
    RunningMeeting,
    schedule_p0_preemption,
    resume_paused_meeting,
)
from src.priority_queue import MeetingQueueItem


def test_p0_pauses_lowest_priority_running_meeting_and_preserves_step() -> None:
    running = (
        RunningMeeting(meeting_id="p1-running", priority="P1", completed_step="routing"),
        RunningMeeting(meeting_id="p3-running", priority="P3", completed_step="context_retrieval"),
    )
    incoming = MeetingQueueItem(meeting_id="incident", priority="P0", created_at=10)

    decision = schedule_p0_preemption(running, incoming, max_concurrent=2)

    assert decision.started.meeting_id == "incident"
    assert decision.paused is not None
    assert decision.paused.meeting_id == "p3-running"
    assert decision.paused.resume_from_step == "context_retrieval"
    assert decision.active_meeting_ids == ("p1-running", "incident")


def test_p0_does_not_pause_when_capacity_is_available() -> None:
    running = (
        RunningMeeting(meeting_id="p1-running", priority="P1", completed_step="routing"),
    )
    incoming = MeetingQueueItem(meeting_id="incident", priority="P0", created_at=10)

    decision = schedule_p0_preemption(running, incoming, max_concurrent=2)

    assert decision.paused is None
    assert decision.active_meeting_ids == ("p1-running", "incident")


def test_resume_paused_meeting_uses_manifest_completed_step() -> None:
    paused_manifest = {
        "meeting_id": "p3-running",
        "priority": "P3",
        "state": "paused",
        "completed_step": "context_retrieval",
        "outputs": ["draft.md"],
    }

    resumed = resume_paused_meeting(paused_manifest)

    assert resumed.meeting_id == "p3-running"
    assert resumed.state == "resuming"
    assert resumed.resume_from_step == "context_retrieval"
    assert resumed.outputs_preserved == ("draft.md",)
