"""Domain priority and concurrency policy for MeetingRun scheduling.

The policy only produces metadata and ordering hints. It deliberately does not
create a custom queue store because Hermes-native Kanban/background/cron
primitives remain the preferred execution substrate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

_URGENCY_SCORE = {"critical": 0, "urgent": 20, "high": 30, "normal": 60, "low": 85}
_CRITICALITY_DISCOUNT = {"critical": 60, "high": 25, "normal": 0, "low": -10}


@dataclass(frozen=True)
class PriorityInput:
    meeting_run_id: str
    urgency: str = "normal"
    criticality: str = "normal"
    created_at: datetime | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PriorityDecision:
    meeting_run_id: str
    priority: str
    sort_key: tuple[int, str]
    score: int
    aging_boost: int
    metadata: dict[str, object]


class PriorityQueuePolicy:
    """Calculate priority as domain metadata for Hermes-native scheduling."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def calculate(self, request: PriorityInput) -> PriorityDecision:
        now = self._now()
        created_at = request.created_at or now
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_hours = max(0.0, (now - created_at).total_seconds() / 3600)
        aging_boost = min(40, int(age_hours // 2) * 10)

        base_score = _URGENCY_SCORE.get(request.urgency, _URGENCY_SCORE["normal"])
        criticality_discount = _CRITICALITY_DISCOUNT.get(
            request.criticality, _CRITICALITY_DISCOUNT["normal"]
        )
        score = max(0, min(99, base_score - criticality_discount - aging_boost))
        priority = self._label_for(score)
        metadata = {
            **request.metadata,
            "scheduling_primitive_preference": "hermes_native",
            "queue_store": "none",
            "priority_policy": "urgency_criticality_aging_v1",
        }
        return PriorityDecision(
            meeting_run_id=request.meeting_run_id,
            priority=priority,
            sort_key=(score, request.meeting_run_id),
            score=score,
            aging_boost=aging_boost,
            metadata=metadata,
        )

    @staticmethod
    def _label_for(score: int) -> str:
        if score < 20:
            return "P0"
        if score < 50:
            return "P1"
        if score < 80:
            return "P2"
        return "P3"


@dataclass(frozen=True)
class ConcurrencyPolicy:
    max_worker: int = 4
    max_validator: int = 2
    max_codex_auditor: int = 1

    def __post_init__(self) -> None:
        if (
            self.max_worker <= 0
            or self.max_validator <= 0
            or self.max_codex_auditor <= 0
        ):
            raise ValueError("concurrency limits must be positive")
        if self.max_codex_auditor >= self.max_worker:
            raise ValueError(
                "codex auditor concurrency must stay below worker concurrency"
            )

    def limit_for(self, role: str) -> int:
        if role == "codex_auditor":
            return self.max_codex_auditor
        if role.endswith("validator") or role == "validation_audit":
            return self.max_validator
        return self.max_worker

    def all_limits(self) -> dict[str, int]:
        return {
            "worker": self.max_worker,
            "validator": self.max_validator,
            "codex_auditor": self.max_codex_auditor,
        }
