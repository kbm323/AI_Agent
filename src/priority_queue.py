"""Priority meeting queue for AC9.

Provides deterministic P0-P1-P2-P3 ordering, FIFO within each priority,
and a max-concurrent dispatch cap.  Pure in-memory module; persistence is
handled by manifest-level components.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class MeetingQueueItem:
    """Queued meeting descriptor."""

    meeting_id: str
    priority: str
    created_at: int | float
    payload: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.meeting_id or not self.meeting_id.strip():
            raise ValueError("meeting_id must be a non-empty string")
        normalized = self.priority.upper()
        if normalized not in _PRIORITY_RANK:
            raise ValueError("priority must be one of P0, P1, P2, P3")
        object.__setattr__(self, "priority", normalized)

    @property
    def sort_key(self) -> tuple[int, int | float, str]:
        """Stable queue ordering key: priority first, then FIFO timestamp."""
        return (_PRIORITY_RANK[self.priority], self.created_at, self.meeting_id)


class PriorityMeetingQueue:
    """In-memory priority queue with explicit running-set accounting."""

    def __init__(self, *, max_concurrent: int = 2) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self._pending: list[MeetingQueueItem] = []
        self._running: list[str] = []

    @property
    def running_ids(self) -> tuple[str, ...]:
        """Currently dispatched meeting IDs, in dispatch order."""
        return tuple(self._running)

    @property
    def pending_ids(self) -> tuple[str, ...]:
        """Pending meeting IDs in effective dispatch order."""
        return tuple(item.meeting_id for item in sorted(self._pending, key=lambda i: i.sort_key))

    def enqueue(self, item: MeetingQueueItem) -> None:
        """Add a meeting to the pending queue."""
        if item.meeting_id in self._running or any(p.meeting_id == item.meeting_id for p in self._pending):
            raise ValueError(f"meeting already tracked: {item.meeting_id}")
        self._pending.append(item)

    def drain_ready_slots(self) -> tuple[MeetingQueueItem, ...]:
        """Dispatch pending meetings until the concurrency cap is reached."""
        slots = self.max_concurrent - len(self._running)
        if slots <= 0 or not self._pending:
            return ()

        ordered = sorted(self._pending, key=lambda i: i.sort_key)
        selected = ordered[:slots]
        selected_ids = {item.meeting_id for item in selected}
        self._pending = [item for item in self._pending if item.meeting_id not in selected_ids]
        self._running.extend(item.meeting_id for item in selected)
        return tuple(selected)

    def mark_completed(self, meeting_id: str) -> None:
        """Remove a running meeting after completion/cancel/failure."""
        self._running = [mid for mid in self._running if mid != meeting_id]
