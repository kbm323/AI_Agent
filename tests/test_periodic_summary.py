"""Tests for periodic summaries and self-reflection reports (AC22)."""

from __future__ import annotations

from src.periodic_summary import generate_periodic_summary


def test_weekly_summary_counts_decisions_and_actions() -> None:
    report = generate_periodic_summary(
        period="weekly",
        meetings=(
            {"meeting_id": "m1", "decisions": ["A"], "action_items": ["x", "y"]},
            {"meeting_id": "m2", "decisions": ["B", "C"], "action_items": []},
        ),
    )

    assert report.period == "weekly"
    assert report.meeting_count == 2
    assert report.decision_count == 3
    assert report.action_item_count == 2
    assert "Self-Reflection" in report.self_reflection
