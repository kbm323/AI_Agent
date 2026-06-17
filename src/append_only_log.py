"""Append-only meeting/decision log for AC21."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class DecisionEvent:
    """Immutable decision log event."""

    event_id: str
    decision_id: str
    content: str
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must be non-empty")
        if not self.decision_id:
            raise ValueError("decision_id must be non-empty")
        if not self.content:
            raise ValueError("content must be non-empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class AppendOnlyDecisionLog:
    """In-memory append-only log with supersession metadata."""

    def __init__(self) -> None:
        self._events: list[DecisionEvent] = []
        self._event_ids: set[str] = set()

    @property
    def events(self) -> tuple[DecisionEvent, ...]:
        return tuple(self._events)

    def append(self, event: DecisionEvent) -> None:
        """Append a new event; never mutates or removes previous events."""
        if event.event_id in self._event_ids:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        self._events.append(event)
        self._event_ids.add(event.event_id)

    def get(self, decision_id: str) -> DecisionEvent:
        """Return the first event for a decision_id."""
        for event in self._events:
            if event.decision_id == decision_id:
                return event
        raise KeyError(decision_id)

    def current_decision_for(self, decision_id: str) -> DecisionEvent:
        """Return latest event that supersedes decision_id, if any."""
        current = self.get(decision_id)
        changed = True
        while changed:
            changed = False
            for event in self._events:
                if event.metadata.get("supersedes") == current.decision_id:
                    current = event
                    changed = True
        return current
