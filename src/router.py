"""Persona routing for the multi-agent meeting system.

Routes decomposed work items to the appropriate execution persona
(OpenClaw/Claude-based executor) and review persona (Hermes-based
reviewer), then coordinates the meeting loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from decomposer import TaskDomain, WorkItem


class PersonaRole(str, Enum):
    EXECUTOR = "executor"   # OpenClaw/Claude-based execution persona
    REVIEWER = "reviewer"   # Hermes-based review persona


@dataclass(frozen=True)
class Persona:
    """A named agent persona with a specific role and domain expertise."""

    name: str
    role: PersonaRole
    domains: tuple[TaskDomain, ...]
    system_prompt: str = ""
    model_hint: str = ""  # preferred model for this persona

    def can_handle(self, domain: TaskDomain) -> bool:
        return domain in self.domains


# ── Default personas ──────────────────────────────────────────────────

EXECUTOR_PERSONAS: tuple[Persona, ...] = (
    Persona(
        name="exec-code",
        role=PersonaRole.EXECUTOR,
        domains=(TaskDomain.CODE, TaskDomain.GENERAL),
        system_prompt=(
            "You are the Code Execution persona in a virtual AI company meeting. "
            "Implement the assigned task precisely. Produce working code. "
            "Document assumptions. Return observable artifacts."
        ),
        model_hint="code",
    ),
    Persona(
        name="exec-content",
        role=PersonaRole.EXECUTOR,
        domains=(TaskDomain.CONTENT,),
        system_prompt=(
            "You are the Content persona. Produce scripts, outlines, "
            "and creative text. Be detailed and structured."
        ),
        model_hint="creative",
    ),
    Persona(
        name="exec-art",
        role=PersonaRole.EXECUTOR,
        domains=(TaskDomain.ART,),
        system_prompt=(
            "You are the Art persona. Describe visual concepts, VFX "
            "approaches, and design directions in concrete, actionable terms."
        ),
        model_hint="creative",
    ),
    Persona(
        name="exec-marketing",
        role=PersonaRole.EXECUTOR,
        domains=(TaskDomain.MARKETING,),
        system_prompt=(
            "You are the Marketing persona. Propose promotion strategies, "
            "SNS plans, and audience engagement tactics."
        ),
        model_hint="creative",
    ),
)

REVIEWER_PERSONA = Persona(
    name="review-hermes",
    role=PersonaRole.REVIEWER,
    domains=(
        TaskDomain.CODE,
        TaskDomain.CONTENT,
        TaskDomain.ART,
        TaskDomain.MARKETING,
        TaskDomain.GENERAL,
    ),
    system_prompt=(
        "You are the Hermes Review persona in a virtual AI company meeting. "
        "Critique the executor's output against the acceptance criteria. "
        "Flag missing pieces, quality issues, and inconsistencies. "
        "Be specific and constructive. If the output is good enough, "
        "approve it. If not, describe exactly what needs to change."
    ),
    model_hint="review",
)


@dataclass(frozen=True)
class RouteAssignment:
    """A work item assigned to a specific persona for execution."""

    work_item: WorkItem
    persona: Persona


@dataclass(frozen=True)
class RoutingResult:
    """Result of routing work items to personas."""

    assignments: tuple[RouteAssignment, ...]
    unassigned: tuple[WorkItem, ...]

    @property
    def executor_assignments(self) -> tuple[RouteAssignment, ...]:
        return tuple(a for a in self.assignments if a.persona.role == PersonaRole.EXECUTOR)

    @property
    def has_reviewable_work(self) -> bool:
        return len(self.executor_assignments) > 0


def select_executor(work_item: WorkItem) -> Persona:
    """Select the best executor persona for a work item."""
    candidates = [p for p in EXECUTOR_PERSONAS if p.can_handle(work_item.domain)]
    if not candidates:
        # fall back to general executor
        candidates = [p for p in EXECUTOR_PERSONAS if TaskDomain.GENERAL in p.domains]
    return candidates[0]


def route_work_items(
    decomposition_result: Any,  # DecompositionResult
) -> RoutingResult:
    """Route decomposed work items to appropriate executor personas.

    All work items go through executor personas.  The review persona
    reviews ALL executor outputs during the meeting loop.
    """
    assignments: list[RouteAssignment] = []
    unassigned: list[WorkItem] = []

    for item in decomposition_result.work_items:
        executor = select_executor(item)
        assignments.append(RouteAssignment(work_item=item, persona=executor))

    return RoutingResult(
        assignments=tuple(assignments),
        unassigned=tuple(unassigned),
    )
