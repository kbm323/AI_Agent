"""User request analysis and task decomposition for the meeting loop.

The decomposer takes a raw user request, analyzes its structure and
priority, then breaks it into discrete work items that can be routed
to execution personas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.shared.token_budget import estimate_token_count
from src.shared.utilities import fingerprint_text


class TaskPriority(str, Enum):
    P0 = "p0"  # blocking, must be done first
    P1 = "p1"  # high priority
    P2 = "p2"  # normal


class TaskDomain(str, Enum):
    CODE = "code"
    CONTENT = "content"
    ART = "art"
    MARKETING = "marketing"
    GENERAL = "general"


@dataclass(frozen=True)
class WorkItem:
    """A single decomposed task from a user request."""

    item_id: str
    title: str
    description: str
    domain: TaskDomain = TaskDomain.GENERAL
    priority: TaskPriority = TaskPriority.P2
    estimated_tokens: int = 0
    dependencies: tuple[str, ...] = ()  # item_ids that must complete first
    acceptance_criteria: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return len(self.dependencies) > 0


@dataclass(frozen=True)
class DecompositionResult:
    """Output of request decomposition."""

    original_request: str
    request_fingerprint: str
    summary: str
    work_items: tuple[WorkItem, ...]
    total_estimated_tokens: int
    priority_order: tuple[int, ...]  # indices into work_items
    requires_human_input: bool = False
    human_input_reason: str = ""

    @property
    def p0_items(self) -> tuple[WorkItem, ...]:
        return tuple(w for w in self.work_items if w.priority == TaskPriority.P0)

    @property
    def ready_items(self) -> tuple[WorkItem, ...]:
        """Items with no unmet dependencies."""
        completed: set[str] = set()  # in practice, tracked externally
        return tuple(
            w for w in self.work_items
            if all(d in completed for d in w.dependencies)
        )


_DOMAIN_KEYWORDS: dict[str, TaskDomain] = {
    "code": TaskDomain.CODE,
    "script": TaskDomain.CODE,
    "api": TaskDomain.CODE,
    "bug": TaskDomain.CODE,
    "test": TaskDomain.CODE,
    "영상": TaskDomain.CONTENT,
    "비디오": TaskDomain.CONTENT,
    "video": TaskDomain.CONTENT,
    "music": TaskDomain.CONTENT,
    "음악": TaskDomain.CONTENT,
    "script": TaskDomain.CONTENT,
    "대본": TaskDomain.CONTENT,
    "art": TaskDomain.ART,
    "design": TaskDomain.ART,
    "graphic": TaskDomain.ART,
    "그림": TaskDomain.ART,
    "디자인": TaskDomain.ART,
    "vfx": TaskDomain.ART,
    "marketing": TaskDomain.MARKETING,
    "sns": TaskDomain.MARKETING,
    "promotion": TaskDomain.MARKETING,
    "마케팅": TaskDomain.MARKETING,
    "홍보": TaskDomain.MARKETING,
}

_PRIORITY_KEYWORDS: dict[str, TaskPriority] = {
    "urgent": TaskPriority.P0,
    "asap": TaskPriority.P0,
    "긴급": TaskPriority.P0,
    "critical": TaskPriority.P0,
    "important": TaskPriority.P1,
    "중요": TaskPriority.P1,
}


def classify_domain(text: str) -> TaskDomain:
    """Infer the task domain from keywords in the description."""
    lower = text.lower()
    scores: dict[TaskDomain, int] = {}
    for kw, domain in _DOMAIN_KEYWORDS.items():
        if kw in lower:
            scores[domain] = scores.get(domain, 0) + 1
    if not scores:
        return TaskDomain.GENERAL
    return max(scores, key=lambda d: scores[d])


def classify_priority(text: str) -> TaskPriority:
    """Infer priority from urgency keywords."""
    lower = text.lower()
    for kw, pri in _PRIORITY_KEYWORDS.items():
        if kw in lower:
            return pri
    return TaskPriority.P2


def decompose_request(
    request: str,
    *,
    max_items: int = 12,
) -> DecompositionResult:
    """Analyze a user request and decompose it into work items.

    This is a deterministic decomposition using keyword heuristics
    and simple text segmentation.  For production use, the LLM-driven
    decomposition inside the meeting loop replaces this with richer
    semantic analysis.
    """
    request_fp = str(fingerprint_text(request))
    sentences = [s.strip() for s in request.replace("\n", ". ").split(".") if s.strip()]

    work_items: list[WorkItem] = []
    for idx, sentence in enumerate(sentences[:max_items]):
        domain = classify_domain(sentence)
        priority = classify_priority(sentence)
        item = WorkItem(
            item_id=f"task-{idx + 1:03d}",
            title=sentence[:80].strip(),
            description=sentence,
            domain=domain,
            priority=priority,
            estimated_tokens=estimate_token_count(sentence),
        )
        work_items.append(item)

    if not work_items:
        work_items.append(
            WorkItem(
                item_id="task-001",
                title=request[:80].strip(),
                description=request,
                domain=classify_domain(request),
                priority=classify_priority(request),
                estimated_tokens=estimate_token_count(request),
            )
        )

    # Build priority-ordered index list (P0 first, then P1, then P2)
    priority_order = sorted(
        range(len(work_items)),
        key=lambda i: (
            {TaskPriority.P0: 0, TaskPriority.P1: 1, TaskPriority.P2: 2}[work_items[i].priority],
            i,
        ),
    )

    total_tokens = sum(w.estimated_tokens for w in work_items)

    return DecompositionResult(
        original_request=request,
        request_fingerprint=request_fp,
        summary=f"Decomposed into {len(work_items)} tasks across "
        f"{len(set(w.domain for w in work_items))} domains",
        work_items=tuple(work_items),
        total_estimated_tokens=total_tokens,
        priority_order=tuple(priority_order),
    )
