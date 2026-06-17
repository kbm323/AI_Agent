"""Same-thread follow-up router for AC26."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class FollowupRoute:
    thread_id: str
    meeting_id: str | None
    action: str
    text: str


def route_followup(
    *,
    thread_id: str,
    text: str,
    thread_to_meeting: Mapping[str, str],
) -> FollowupRoute:
    if not thread_id:
        raise ValueError("thread_id must be non-empty")
    meeting_id = thread_to_meeting.get(thread_id)
    if meeting_id:
        return FollowupRoute(
            thread_id=thread_id,
            meeting_id=meeting_id,
            action="extend_existing_meeting",
            text=text,
        )
    return FollowupRoute(
        thread_id=thread_id,
        meeting_id=None,
        action="create_new_meeting",
        text=text,
    )
