"""Discord delivery planning for AC25.

No live Discord calls; returns a pure delivery plan consumed by adapters.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadMessage:
    thread_id: str
    content: str


@dataclass(frozen=True)
class ChannelMessage:
    channel_id: str
    content: str


@dataclass(frozen=True)
class DiscordDeliveryPlan:
    meeting_id: str
    primary: ThreadMessage
    cross_post: ChannelMessage


def plan_discord_delivery(
    *,
    meeting_id: str,
    original_thread_id: str,
    response: str,
    summary: str,
    result_channel_id: str,
) -> DiscordDeliveryPlan:
    if not meeting_id or not original_thread_id or not result_channel_id:
        raise ValueError("meeting_id, original_thread_id, and result_channel_id are required")
    return DiscordDeliveryPlan(
        meeting_id=meeting_id,
        primary=ThreadMessage(thread_id=original_thread_id, content=response),
        cross_post=ChannelMessage(channel_id=result_channel_id, content=summary),
    )
