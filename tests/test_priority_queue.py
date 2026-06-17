"""Tests for priority meeting queue (AC9).

AC9: P0-P1-P2-P3 priority queue with FIFO within same priority,
max 2 concurrent meetings.
"""

from __future__ import annotations

import pytest

from src.priority_queue import MeetingQueueItem, PriorityMeetingQueue


def test_priority_ordering_with_fifo_inside_same_priority() -> None:
    queue = PriorityMeetingQueue()
    queue.enqueue(MeetingQueueItem(meeting_id="p2-a", priority="P2", created_at=1))
    queue.enqueue(MeetingQueueItem(meeting_id="p1-a", priority="P1", created_at=2))
    queue.enqueue(MeetingQueueItem(meeting_id="p1-b", priority="P1", created_at=3))
    queue.enqueue(MeetingQueueItem(meeting_id="p0-a", priority="P0", created_at=4))
    queue.enqueue(MeetingQueueItem(meeting_id="p3-a", priority="P3", created_at=5))

    first = queue.drain_ready_slots()
    assert [item.meeting_id for item in first] == ["p0-a", "p1-a"]
    for item in first:
        queue.mark_completed(item.meeting_id)

    second = queue.drain_ready_slots()
    assert [item.meeting_id for item in second] == ["p1-b", "p2-a"]
    for item in second:
        queue.mark_completed(item.meeting_id)

    assert [item.meeting_id for item in queue.drain_ready_slots()] == ["p3-a"]


def test_max_two_concurrent_meetings_blocks_extra_dispatch() -> None:
    queue = PriorityMeetingQueue(max_concurrent=2)
    queue.enqueue(MeetingQueueItem(meeting_id="a", priority="P1", created_at=1))
    queue.enqueue(MeetingQueueItem(meeting_id="b", priority="P1", created_at=2))
    queue.enqueue(MeetingQueueItem(meeting_id="c", priority="P1", created_at=3))

    first = queue.drain_ready_slots()

    assert [item.meeting_id for item in first] == ["a", "b"]
    assert queue.running_ids == ("a", "b")
    assert queue.drain_ready_slots() == ()

    queue.mark_completed("a")
    second = queue.drain_ready_slots()

    assert [item.meeting_id for item in second] == ["c"]
    assert queue.running_ids == ("b", "c")


def test_priority_values_are_validated() -> None:
    with pytest.raises(ValueError, match="priority"):
        MeetingQueueItem(meeting_id="bad", priority="P9", created_at=1)
