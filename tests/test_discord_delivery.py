"""Tests for Discord thread delivery and cross-posting (AC25)."""

from __future__ import annotations

from src.discord_delivery import plan_discord_delivery


def test_response_delivered_to_original_thread_and_summary_cross_posted() -> None:
    plan = plan_discord_delivery(
        meeting_id="m1",
        original_thread_id="thread-1",
        response="full response",
        summary="short summary",
        result_channel_id="results",
    )

    assert plan.primary.thread_id == "thread-1"
    assert plan.primary.content == "full response"
    assert plan.cross_post.channel_id == "results"
    assert plan.cross_post.content == "short summary"
