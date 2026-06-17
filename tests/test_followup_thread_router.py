"""Tests for same-thread follow-up routing (AC26)."""

from __future__ import annotations

from src.followup_thread_router import route_followup


def test_same_thread_followup_extends_existing_meeting_id() -> None:
    existing = {"thread-1": "meeting-123"}

    route = route_followup(thread_id="thread-1", text="추가 질문", thread_to_meeting=existing)

    assert route.meeting_id == "meeting-123"
    assert route.action == "extend_existing_meeting"


def test_new_thread_creates_new_meeting() -> None:
    route = route_followup(thread_id="thread-2", text="새 회의", thread_to_meeting={})

    assert route.meeting_id is None
    assert route.action == "create_new_meeting"
