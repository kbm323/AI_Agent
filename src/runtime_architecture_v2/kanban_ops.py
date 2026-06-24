"""Phase 16 Hermes-native Kanban operation planning.

This module converts AI_Agent MeetingRun domain artifacts into a deterministic
Hermes Kanban card graph. It intentionally does not implement a queue database
or mutate Hermes Core. Live Kanban creation is only possible through an injected
client boundary; dry-run remains the default pilot path.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from .knowledge import retrieve_knowledge_context
from .multi_bot import MultiBotSession, run_phase14_multi_bot_pilot
from .queue_policy import ConcurrencyPolicy, PriorityInput, PriorityQueuePolicy
from .scheduling_policy import SchedulingPolicy, SchedulingRequest
from .schemas import MeetingRun, WorkerTask
from .store import MeetingRunStore

_TOKEN_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*"
        r"[^\s\\`'\"]+"
    ),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{6,}"),
)
_SANITIZATION_RULES = (
    "redact secret-like key/value pairs",
    "redact bearer tokens",
    "redact uncontrolled @everyone/@here mentions",
)
_MENTION_RE = re.compile(r"@(everyone|here)\b", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class KanbanClient(Protocol):
    """Injected live boundary for Hermes Kanban card creation."""

    def create_card(
        self,
        *,
        title: str,
        body: str,
        assignee: str,
        priority: str,
        parents: list[str],
        metadata: dict[str, object],
    ) -> str: ...


@dataclass(frozen=True)
class KanbanCardSpec:
    """One planned Hermes Kanban card."""

    card_id: str
    meeting_run_id: str
    kind: str
    title: str
    body: str
    assignee: str
    priority: str
    parents: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_safe_id(self.card_id, "card_id")
        _validate_safe_id(self.meeting_run_id, "meeting_run_id")
        object.__setattr__(self, "parents", tuple(self.parents))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "meeting_run_id": self.meeting_run_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "assignee": self.assignee,
            "priority": self.priority,
            "parents": list(self.parents),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class KanbanOperationPlan:
    """Deterministic Kanban card graph for one MeetingRun."""

    ok: bool
    meeting_run_id: str
    cards: tuple[KanbanCardSpec, ...]
    scheduling_decision: Any
    priority_decision: Any
    concurrency_limits: dict[str, int]
    plan_path: Path | None = None
    error: str = ""
    requires_custom_queue_store: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "meeting_run_id": self.meeting_run_id,
            "cards": [card.to_dict() for card in self.cards],
            "scheduling_decision": self.scheduling_decision.to_dict(),
            "priority_decision": {
                "meeting_run_id": self.priority_decision.meeting_run_id,
                "priority": self.priority_decision.priority,
                "sort_key": list(self.priority_decision.sort_key),
                "score": self.priority_decision.score,
                "aging_boost": self.priority_decision.aging_boost,
                "metadata": self.priority_decision.metadata,
            },
            "concurrency_limits": self.concurrency_limits,
            "requires_custom_queue_store": self.requires_custom_queue_store,
            "plan_path": str(self.plan_path) if self.plan_path else "",
            "error": self.error,
        }


@dataclass(frozen=True)
class KanbanDispatchResult:
    """Result of applying or dry-running a Kanban operation plan."""

    ok: bool
    meeting_run_id: str
    dry_run: bool
    created_refs: tuple[str, ...]
    local_to_remote_refs: dict[str, str]
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "meeting_run_id": self.meeting_run_id,
            "dry_run": self.dry_run,
            "created_refs": list(self.created_refs),
            "local_to_remote_refs": self.local_to_remote_refs,
            "error": self.error,
        }


def build_kanban_operation_plan(
    *,
    root: str | Path,
    meeting_run: MeetingRun,
    session: MultiBotSession,
    worker_tasks: tuple[WorkerTask, ...],
    knowledge_query: str = "버추얼 아이돌 팬 참여 쇼츠 데뷔",
    knowledge_context: str = "",
    phase: str = "phase16",
) -> KanbanOperationPlan:
    """Build a Hermes Kanban fan-out/fan-in plan for a MeetingRun."""

    _ = root
    _validate_safe_id(meeting_run.meeting_run_id, "meeting_run_id")
    _validate_safe_id(phase, "phase")
    if meeting_run.meeting_run_id != session.meeting_run_id:
        raise ValueError("meeting_run_id mismatch between MeetingRun and session")
    for task in worker_tasks:
        if task.meeting_run_id != meeting_run.meeting_run_id:
            raise ValueError("worker task meeting_run_id mismatch")

    priority_decision = PriorityQueuePolicy().calculate(
        PriorityInput(
            meeting_run_id=meeting_run.meeting_run_id,
            urgency=_priority_to_urgency(meeting_run.priority),
            criticality="normal",
            metadata={"phase": phase},
        )
    )
    scheduling_decision = SchedulingPolicy().decide(
        SchedulingRequest(
            meeting_run_id=meeting_run.meeting_run_id,
            route_type="kanban_operations",
            durable=True,
            long_running=False,
            scheduled=False,
            retryable=False,
            simulation=False,
        )
    )
    concurrency = ConcurrencyPolicy()
    safe_context = _sanitize_text(
        knowledge_context
        or retrieve_knowledge_context(
            root=root, query=knowledge_query, limit=2
        ).context_markdown
        or "No prior knowledge context matched."
    )

    common_metadata: dict[str, object] = {
        **priority_decision.metadata,
        "phase": phase,
        "scheduling_primitive": scheduling_decision.hermes_primitive,
        "scheduling_kind": scheduling_decision.kind.value,
        "queue_store": "none",
        "requires_custom_queue_store": False,
        "sanitization_rules": list(_SANITIZATION_RULES),
    }

    cards: list[KanbanCardSpec] = []
    for index, task in enumerate(worker_tasks, start=1):
        card_id = f"kb_{meeting_run.meeting_run_id}_{index}_{task.role}"
        role_limit = concurrency.limit_for(task.role)
        body = _build_worker_card_body(
            meeting_run=meeting_run,
            session=session,
            task=task,
            knowledge_context=safe_context,
            role_limit=role_limit,
        )
        cards.append(
            KanbanCardSpec(
                card_id=card_id,
                meeting_run_id=meeting_run.meeting_run_id,
                kind="worker",
                title=f"[{meeting_run.meeting_run_id}] {task.role} 실행 과제",
                body=body,
                assignee=task.role,
                priority=priority_decision.priority,
                parents=(),
                metadata={
                    **common_metadata,
                    "worker_task_id": task.worker_task_id,
                    "role_concurrency_limit": role_limit,
                },
            )
        )

    parent_ids = tuple(card.card_id for card in cards)
    review_body = _build_review_card_body(
        meeting_run=meeting_run,
        session=session,
        knowledge_context=safe_context,
    )
    cards.append(
        KanbanCardSpec(
            card_id=f"kb_{meeting_run.meeting_run_id}_review",
            meeting_run_id=meeting_run.meeting_run_id,
            kind="review",
            title=f"[{meeting_run.meeting_run_id}] fan-in 검증/보고",
            body=review_body,
            assignee="validation_audit",
            priority=priority_decision.priority,
            parents=parent_ids,
            metadata={
                **common_metadata,
                "role_concurrency_limit": concurrency.limit_for("validation_audit"),
                "parent_card_count": len(parent_ids),
            },
        )
    )

    return KanbanOperationPlan(
        ok=True,
        meeting_run_id=meeting_run.meeting_run_id,
        cards=tuple(cards),
        scheduling_decision=scheduling_decision,
        priority_decision=priority_decision,
        concurrency_limits=concurrency.all_limits(),
        requires_custom_queue_store=False,
    )


def dispatch_kanban_operation_plan(
    plan: KanbanOperationPlan,
    *,
    client: KanbanClient | None = None,
    dry_run: bool = True,
) -> KanbanDispatchResult:
    """Dry-run or apply a Kanban operation plan through an injected client."""

    local_to_remote: dict[str, str] = {}
    created_refs: list[str] = []

    if dry_run:
        for card in plan.cards:
            ref = f"dry_run:{card.card_id}"
            local_to_remote[card.card_id] = ref
            created_refs.append(ref)
        return KanbanDispatchResult(
            ok=True,
            meeting_run_id=plan.meeting_run_id,
            dry_run=True,
            created_refs=tuple(created_refs),
            local_to_remote_refs=local_to_remote,
        )

    if client is None:
        return KanbanDispatchResult(
            ok=False,
            meeting_run_id=plan.meeting_run_id,
            dry_run=False,
            created_refs=(),
            local_to_remote_refs={},
            error="kanban_client_required",
        )

    try:
        for card in plan.cards:
            remote_parents = [local_to_remote[parent] for parent in card.parents]
            remote_ref = client.create_card(
                title=card.title,
                body=card.body,
                assignee=card.assignee,
                priority=card.priority,
                parents=remote_parents,
                metadata=card.metadata,
            )
            local_to_remote[card.card_id] = str(remote_ref)
            created_refs.append(str(remote_ref))
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return KanbanDispatchResult(
            ok=False,
            meeting_run_id=plan.meeting_run_id,
            dry_run=False,
            created_refs=tuple(created_refs),
            local_to_remote_refs=local_to_remote,
            error="kanban_dispatch_failed",
        )

    return KanbanDispatchResult(
        ok=True,
        meeting_run_id=plan.meeting_run_id,
        dry_run=False,
        created_refs=tuple(created_refs),
        local_to_remote_refs=local_to_remote,
    )


def run_phase16_kanban_pilot(
    *,
    root: str | Path,
    mode: Literal["dry-run"] = "dry-run",
    knowledge_query: str = "버추얼 아이돌 팬 참여 쇼츠 데뷔",
) -> dict[str, Any]:
    """Run the deterministic Phase 16 Kanban operation pilot."""

    if mode != "dry-run":
        return {
            "ok": False,
            "pilot_id": "phase16_autonomous_scheduling_kanban_operations",
            "mode": mode,
            "error": "phase16_only_supports_dry_run",
        }

    root = Path(root)
    phase14 = run_phase14_multi_bot_pilot(root=root, mode="dry-run")
    plan = build_kanban_operation_plan(
        root=root,
        meeting_run=phase14.meeting_run,
        session=phase14.session,
        worker_tasks=phase14.worker_tasks,
        knowledge_query=knowledge_query,
    )
    dispatch = dispatch_kanban_operation_plan(plan, dry_run=True)
    plan_path = _write_plan_artifact(root, plan, dispatch)
    plan = replace(plan, plan_path=plan_path)

    updated_metadata = {
        **phase14.meeting_run.metadata,
        "phase16_kanban_plan_path": str(plan_path.relative_to(root)),
        "phase16_kanban_card_refs": list(dispatch.created_refs),
        "phase16_requires_custom_queue_store": False,
    }
    updated_run = replace(
        phase14.meeting_run,
        hermes_refs={
            **phase14.meeting_run.hermes_refs,
            "kanban_plan_path": str(plan_path.relative_to(root)),
        },
        metadata=updated_metadata,
    )
    MeetingRunStore(root).save_meeting_run(updated_run)

    return {
        "ok": plan.ok and dispatch.ok,
        "pilot_id": "phase16_autonomous_scheduling_kanban_operations",
        "mode": mode,
        "meeting_run_id": plan.meeting_run_id,
        "kanban_card_count": len(plan.cards),
        "kanban_card_ids": [card.card_id for card in plan.cards],
        "kanban_cards": [card.to_dict() for card in plan.cards],
        "dispatch_dry_run": dispatch.dry_run,
        "created_refs": list(dispatch.created_refs),
        "requires_custom_queue_store": plan.requires_custom_queue_store,
        "plan_path": str(plan_path),
        "error": dispatch.error or plan.error,
    }


def _build_worker_card_body(
    *,
    meeting_run: MeetingRun,
    session: MultiBotSession,
    task: WorkerTask,
    knowledge_context: str,
    role_limit: int,
) -> str:
    trigger = _sanitize_text(str(meeting_run.trigger.get("text") or ""))
    summary = _sanitize_text(
        session.consensus_summary or "No consensus summary recorded."
    )
    return (
        f"MeetingRun: `{meeting_run.meeting_run_id}`\n\n"
        f"Role: `{task.role}`\n\n"
        f"WorkerTask: `{task.worker_task_id}`\n\n"
        f"Concurrency limit for role: `{role_limit}`\n\n"
        "Objective:\n"
        f"- Execute the role-specific next step for: {trigger}\n\n"
        "Consensus summary:\n"
        f"- {summary}\n\n"
        "Prior knowledge context:\n"
        f"{knowledge_context}\n\n"
        "Guardrails:\n"
        "- Use Hermes-native Kanban as the execution substrate.\n"
        "- Do not create a custom queue database.\n"
        "- Return concise completion evidence for the fan-in review card.\n"
    )


def _build_review_card_body(
    *,
    meeting_run: MeetingRun,
    session: MultiBotSession,
    knowledge_context: str,
) -> str:
    summary = _sanitize_text(
        session.consensus_summary or "No consensus summary recorded."
    )
    return (
        f"MeetingRun: `{meeting_run.meeting_run_id}`\n\n"
        "Objective:\n"
        "- Review all parent worker cards, resolve conflicts, "
        "and produce final report.\n\n"
        "Consensus summary:\n"
        f"- {summary}\n\n"
        "Prior knowledge context:\n"
        f"{knowledge_context}\n\n"
        "Guardrails:\n"
        "- Parent worker evidence is required before approval.\n"
        "- Escalate instead of silently passing missing evidence.\n"
        "- Keep queue_store=none; Hermes Kanban owns task state.\n"
    )


def _write_plan_artifact(
    root: Path, plan: KanbanOperationPlan, dispatch: KanbanDispatchResult) -> Path:
    path = root / "runtime" / "phase16-kanban" / f"{plan.meeting_run_id}.json"
    payload = {"plan": plan.to_dict(), "dispatch": dispatch.to_dict()}
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
    )
    return path


def _priority_to_urgency(priority: str) -> str:
    return {"P0": "critical", "P1": "high", "P2": "normal", "P3": "low"}.get(
        priority, "normal"
    )


def _sanitize_text(text: str) -> str:
    safe = text
    for pattern in _TOKEN_PATTERNS:
        safe = pattern.sub("[REDACTED_SECRET]", safe)
    return _MENTION_RE.sub("@[redacted-mention]", safe)


def _validate_safe_id(value: str, label: str) -> None:
    if not value or value in {".", ".."} or value.startswith("."):
        raise ValueError(f"unsafe {label}: {value}")
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value}")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
