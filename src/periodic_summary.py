"""Periodic summaries and self-reflection reports for AC22."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

_VALID_PERIODS = {"weekly", "monthly", "quarterly"}


@dataclass(frozen=True)
class PeriodicSummary:
    period: str
    meeting_count: int
    decision_count: int
    action_item_count: int
    self_reflection: str


def _count_sequence(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set, frozenset)):
        return len(value)
    return 1


def generate_periodic_summary(
    *,
    period: str,
    meetings: Sequence[Mapping[str, object]],
) -> PeriodicSummary:
    normalized = period.lower().strip()
    if normalized not in _VALID_PERIODS:
        raise ValueError("period must be weekly, monthly, or quarterly")
    decision_count = sum(_count_sequence(m.get("decisions")) for m in meetings)
    action_item_count = sum(_count_sequence(m.get("action_items")) for m in meetings)
    reflection = (
        f"Self-Reflection Report: {len(meetings)} meetings reviewed; "
        f"{decision_count} decisions and {action_item_count} action items tracked."
    )
    return PeriodicSummary(
        period=normalized,
        meeting_count=len(meetings),
        decision_count=decision_count,
        action_item_count=action_item_count,
        self_reflection=reflection,
    )
