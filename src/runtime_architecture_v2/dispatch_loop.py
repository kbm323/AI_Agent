"""Phase 18 Live Kanban Autonomous Dispatch Loop.

This module extends Phase 16's Kanban operation plan with a live dispatch,
monitor, and recovery loop. It intentionally does not implement a custom
queue database or mutate Hermes Core — the KanbanClient boundary abstracts
live Hermes Kanban interactions.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .kanban_ops import (
    KanbanCardSpec,
    KanbanClient,
    KanbanDispatchResult,
    KanbanOperationPlan,
    _sanitize_text,
)
from .multi_bot import MultiBotSession, run_phase14_multi_bot_pilot
from .queue_policy import ConcurrencyPolicy, PriorityInput, PriorityQueuePolicy
from .scheduling_policy import SchedulingPolicy, SchedulingRequest
from .schemas import MeetingRun, WorkerTask
from .store import MeetingRunStore, StoreError

AUTONOMOUS_DISPATCH_LOOP_ID = "phase18_live_kanban_autonomous_dispatch"

_BLOCKED_RECOVERABLE_STATES = frozenset({"blocked", "failed"})
_TERMINAL_CARD_STATES = frozenset({"completed"})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KanbanCardStatus:
    """Live Kanban card observable state snapshot."""

    card_id: str
    meeting_run_id: str
    kind: str
    state: str
    claimed_by: str
    completed_at: datetime | None
    blocked_reason: str
    reclaim_count: int
    age_hours: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blocked_reason",
            _sanitize_text(self.blocked_reason),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "meeting_run_id": self.meeting_run_id,
            "kind": self.kind,
            "state": self.state,
            "claimed_by": self.claimed_by,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "blocked_reason": self.blocked_reason,
            "reclaim_count": self.reclaim_count,
            "age_hours": round(self.age_hours, 1),
        }


@dataclass(frozen=True)
class RecoveryAction:
    """One recovery action for a stuck or blocked card."""

    card_id: str
    action: str
    reason: str
    target_assignee: str

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "action": self.action,
            "reason": self.reason,
            "target_assignee": self.target_assignee,
        }


@dataclass(frozen=True)
class DispatchLoopResult:
    """Result of one AutonomousDispatchLoop round."""

    ok: bool
    dry_run: bool
    meeting_run_id: str
    round_number: int
    dispatched_count: int
    claimed_count: int
    completed_count: int
    blocked_count: int
    failed_count: int
    recovered_count: int
    escalated_count: int
    converged: bool
    card_statuses: tuple[KanbanCardStatus, ...] = ()
    recovery_actions: tuple[RecoveryAction, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "meeting_run_id": self.meeting_run_id,
            "round_number": self.round_number,
            "dispatched_count": self.dispatched_count,
            "claimed_count": self.claimed_count,
            "completed_count": self.completed_count,
            "blocked_count": self.blocked_count,
            "failed_count": self.failed_count,
            "recovered_count": self.recovered_count,
            "escalated_count": self.escalated_count,
            "converged": self.converged,
            "card_statuses": [cs.to_dict() for cs in self.card_statuses],
            "recovery_actions": [ra.to_dict() for ra in self.recovery_actions],
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# AutonomousDispatchLoop
# ---------------------------------------------------------------------------


class AutonomousDispatchLoop:
    """Dispatch Kanban plans, monitor card states, and recover stuck cards."""

    def __init__(
        self,
        *,
        root: str | Path,
        client: KanbanClient | None = None,
        dry_run: bool = True,
        max_rounds: int = 10,
        max_reclaim_attempts: int = 2,
    ) -> None:
        self.root = Path(root)
        self.client = client
        self.dry_run = dry_run
        self.max_rounds = max_rounds
        self.max_reclaim_attempts = max_reclaim_attempts
        self._round = 0

    def run(
        self,
        *,
        meeting_run: MeetingRun,
        session: MultiBotSession,
        worker_tasks: tuple[WorkerTask, ...],
        knowledge_query: str = "",
        prior_card_statuses: tuple[KanbanCardStatus, ...] = (),
    ) -> DispatchLoopResult:
        """Execute one round of dispatch→poll→recover for a MeetingRun."""

        self._round += 1
        prior_map: dict[str, KanbanCardStatus] = {
            cs.card_id: cs for cs in prior_card_statuses
        }

        # Build plan (Phase 16)
        priority_decision = PriorityQueuePolicy().calculate(
            PriorityInput(
                meeting_run_id=meeting_run.meeting_run_id,
                urgency=_priority_to_urgency(meeting_run.priority),
                criticality="normal",
                metadata={"phase": "phase18"},
            ),
        )
        scheduling_decision = SchedulingPolicy().decide(
            SchedulingRequest(
                meeting_run_id=meeting_run.meeting_run_id,
                route_type="autonomous_dispatch",
                durable=True,
                long_running=False,
                scheduled=False,
                retryable=True,
                simulation=False,
            ),
        )
        concurrency = ConcurrencyPolicy()
        common_metadata: dict[str, object] = {
            "phase": "phase18",
            "scheduling_kind": scheduling_decision.kind.value,
            "scheduling_primitive": scheduling_decision.hermes_primitive,
            "queue_store": "none",
            "requires_custom_queue_store": False,
        }

        cards: list[KanbanCardSpec] = []
        for index, task in enumerate(worker_tasks, start=1):
            card_id = f"kb_{meeting_run.meeting_run_id}_{index}_{task.role}"
            role_limit = concurrency.limit_for(task.role)
            cards.append(
                KanbanCardSpec(
                    card_id=card_id,
                    meeting_run_id=meeting_run.meeting_run_id,
                    kind="worker",
                    title=f"[{meeting_run.meeting_run_id}] {task.role}",
                    body=f"Worker task for {task.role}",
                    assignee=task.role,
                    priority=priority_decision.priority,
                    parents=(),
                    metadata={
                        **common_metadata,
                        "worker_task_id": task.worker_task_id,
                        "role_concurrency_limit": role_limit,
                    },
                ),
            )

        parent_ids = tuple(c.card_id for c in cards)
        cards.append(
            KanbanCardSpec(
                card_id=f"kb_{meeting_run.meeting_run_id}_review",
                meeting_run_id=meeting_run.meeting_run_id,
                kind="review",
                title=f"[{meeting_run.meeting_run_id}] review",
                body="Fan-in review card",
                assignee="validation_audit",
                priority=priority_decision.priority,
                parents=parent_ids,
                metadata={
                    **common_metadata,
                    "role_concurrency_limit": concurrency.limit_for("validation_audit"),
                    "parent_card_count": len(parent_ids),
                },
            ),
        )

        plan = KanbanOperationPlan(
            ok=True,
            meeting_run_id=meeting_run.meeting_run_id,
            cards=tuple(cards),
            scheduling_decision=scheduling_decision,
            priority_decision=priority_decision,
            concurrency_limits=concurrency.all_limits(),
            requires_custom_queue_store=False,
        )

        # Dispatch
        dispatch_result = self._apply_dispatch(plan)
        if not dispatch_result.ok:
            return DispatchLoopResult(
                ok=False,
                dry_run=self.dry_run,
                meeting_run_id=meeting_run.meeting_run_id,
                round_number=self._round,
                dispatched_count=0,
                claimed_count=0,
                completed_count=0,
                blocked_count=0,
                failed_count=0,
                recovered_count=0,
                escalated_count=0,
                converged=False,
                error=dispatch_result.error,
            )

        # Poll / simulate card statuses
        card_statuses = self._poll_statuses(
            plan=plan,
            prior_map=prior_map,
            round_number=self._round,
        )

        # Recovery
        recovery_actions = self._recover_cards(
            card_statuses=tuple(card_statuses),
            round_number=self._round,
        )

        return self._build_result(
            plan=plan,
            dispatch=dispatch_result,
            card_statuses=tuple(card_statuses),
            recovery_actions=tuple(recovery_actions),
            round_number=self._round,
        )

    # --- internal helpers ---

    def _apply_dispatch(self, plan: KanbanOperationPlan) -> KanbanDispatchResult:
        if self.dry_run:
            return self._dry_dispatch(plan)
        if self.client is None:
            return KanbanDispatchResult(
                ok=False,
                meeting_run_id=plan.meeting_run_id,
                dry_run=False,
                created_refs=(),
                local_to_remote_refs={},
                error="kanban_client_required",
            )
        local_to_remote: dict[str, str] = {}
        created_refs: list[str] = []
        try:
            for card in plan.cards:
                remote_parents = [local_to_remote[p] for p in card.parents]
                ref = self.client.create_card(
                    title=card.title,
                    body=card.body,
                    assignee=card.assignee,
                    priority=card.priority,
                    parents=remote_parents,
                    metadata=card.metadata,
                )
                local_to_remote[card.card_id] = str(ref)
                created_refs.append(str(ref))
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

    @staticmethod
    def _dry_dispatch(plan: KanbanOperationPlan) -> KanbanDispatchResult:
        refs = tuple(f"dry_run:{c.card_id}" for c in plan.cards)
        return KanbanDispatchResult(
            ok=True,
            meeting_run_id=plan.meeting_run_id,
            dry_run=True,
            created_refs=refs,
            local_to_remote_refs=dict(
                zip(
                    (c.card_id for c in plan.cards),
                    refs,
                    strict=False,
                )
            ),
        )

    def _poll_statuses(
        self,
        *,
        plan: KanbanOperationPlan,
        prior_map: dict[str, KanbanCardStatus],
        round_number: int,
    ) -> list[KanbanCardStatus]:
        statuses: list[KanbanCardStatus] = []
        now = datetime.now(UTC)
        for card in plan.cards:
            prior = prior_map.get(card.card_id)
            if self.dry_run:
                statuses.append(
                    self._simulate_card_status(
                        card=card,
                        prior=prior,
                        round_number=round_number,
                        now=now,
                    ),
                )
            else:
                statuses.append(
                    self._live_card_status(
                        card=card,
                        prior=prior,
                        now=now,
                    ),
                )
        return statuses

    @staticmethod
    def _simulate_card_status(
        *,
        card: KanbanCardSpec,
        prior: KanbanCardStatus | None,
        round_number: int,
        now: datetime,
    ) -> KanbanCardStatus:
        """Dry-run simulation: cards progress pending→claimed→completed."""
        if prior is not None and prior.state == "completed":
            return prior

        if prior is not None and prior.state == "claimed":
            return KanbanCardStatus(
                card_id=card.card_id,
                meeting_run_id=card.meeting_run_id,
                kind=card.kind,
                state="completed",
                claimed_by=prior.claimed_by,
                completed_at=now,
                blocked_reason="",
                reclaim_count=prior.reclaim_count,
                age_hours=prior.age_hours + 1.0,
            )

        return KanbanCardStatus(
            card_id=card.card_id,
            meeting_run_id=card.meeting_run_id,
            kind=card.kind,
            state="claimed",
            claimed_by=card.assignee,
            completed_at=None,
            blocked_reason="",
            reclaim_count=0,
            age_hours=float(round_number),
        )

    @staticmethod
    def _live_card_status(
        *,
        card: KanbanCardSpec,
        prior: KanbanCardStatus | None,
        now: datetime,
    ) -> KanbanCardStatus:
        """Live card status — placeholder when no live Kanban API is connected."""
        if prior is not None:
            return prior
        return KanbanCardStatus(
            card_id=card.card_id,
            meeting_run_id=card.meeting_run_id,
            kind=card.kind,
            state="pending",
            claimed_by="",
            completed_at=None,
            blocked_reason="",
            reclaim_count=0,
            age_hours=0.0,
        )

    def _recover_cards(
        self,
        *,
        card_statuses: tuple[KanbanCardStatus, ...],
        round_number: int,
    ) -> list[RecoveryAction]:
        actions: list[RecoveryAction] = []
        for cs in card_statuses:
            if cs.state not in _BLOCKED_RECOVERABLE_STATES:
                continue
            if cs.reclaim_count >= self.max_reclaim_attempts:
                actions.append(
                    RecoveryAction(
                        card_id=cs.card_id,
                        action="escalate",
                        reason=(
                            f"max reclaim attempts ({self.max_reclaim_attempts}) "
                            f"exceeded; round {round_number}"
                        ),
                        target_assignee="orchestrator",
                    ),
                )
            else:
                assignee = cs.claimed_by or "orchestrator"
                actions.append(
                    RecoveryAction(
                        card_id=cs.card_id,
                        action="reassign",
                        reason=(
                            f"card {cs.state} at round {round_number}; "
                            f"reclaim_count={cs.reclaim_count}"
                        ),
                        target_assignee=assignee,
                    ),
                )
        return actions

    @staticmethod
    def _build_result(
        *,
        plan: KanbanOperationPlan,
        dispatch: KanbanDispatchResult,
        card_statuses: tuple[KanbanCardStatus, ...],
        recovery_actions: tuple[RecoveryAction, ...],
        round_number: int,
    ) -> DispatchLoopResult:
        dispatched = len(dispatch.created_refs)
        claimed = sum(1 for cs in card_statuses if cs.state == "claimed")
        completed = sum(1 for cs in card_statuses if cs.state == "completed")
        blocked = sum(1 for cs in card_statuses if cs.state == "blocked")
        failed = sum(1 for cs in card_statuses if cs.state == "failed")
        recovered = sum(1 for ra in recovery_actions if ra.action == "reassign")
        escalated = sum(1 for ra in recovery_actions if ra.action == "escalate")
        pending = sum(1 for cs in card_statuses if cs.state == "pending")

        total_cards = len(card_statuses)
        converged = (
            completed >= total_cards and blocked == 0 and failed == 0 and pending == 0
        )

        final_meeting_run_id = plan.meeting_run_id or dispatch.meeting_run_id
        dispatch_ok = dispatch.ok and failed == 0 and blocked == 0
        return DispatchLoopResult(
            ok=dispatch_ok,
            dry_run=dispatch.dry_run,
            meeting_run_id=final_meeting_run_id,
            round_number=round_number,
            dispatched_count=dispatched,
            claimed_count=claimed,
            completed_count=completed,
            blocked_count=blocked,
            failed_count=failed,
            recovered_count=recovered,
            escalated_count=escalated,
            converged=converged,
            card_statuses=card_statuses,
            recovery_actions=recovery_actions,
            error=dispatch.error,
        )


# ---------------------------------------------------------------------------
# Phase 18 CLI Pilot
# ---------------------------------------------------------------------------


def run_phase18_autonomous_dispatch(
    *,
    root: str | Path,
    mode: Literal["dry-run", "live"] = "dry-run",
    meeting_run_id: str = "",
    max_rounds: int = 3,
    client: KanbanClient | None = None,
) -> dict[str, Any]:
    """Run the Phase 18 autonomous dispatch pilot."""

    if mode not in ("dry-run", "live"):
        return {
            "ok": False,
            "pilot_id": AUTONOMOUS_DISPATCH_LOOP_ID,
            "mode": mode,
            "error": f"unsupported mode: {mode}",
        }

    root = Path(root)
    store = MeetingRunStore(root)

    if mode == "live" and client is None:
        mid = meeting_run_id or "mr-phase18-auto"
        return {
            "ok": False,
            "pilot_id": AUTONOMOUS_DISPATCH_LOOP_ID,
            "mode": mode,
            "meeting_run_id": mid,
            "error": "kanban_client_required",
        }

    # Create or load a MeetingRun
    if mode == "dry-run":
        default_mid = meeting_run_id or "mr-phase18-dryrun"
        try:
            meeting_run = store.load_meeting_run(default_mid)
        except (OSError, ValueError, StoreError):
            meeting_run = MeetingRun.create(
                meeting_run_id=default_mid,
                trigger_text="팬 참여 쇼츠 데뷔 전략 (Phase 18)",
                user_id="u-phase18",
                channel_id="ch-phase18",
                thread_id="th-phase18",
                priority="P1",
            )
            store.save_meeting_run(meeting_run)
    else:
        default_mid = meeting_run_id or "mr-phase18-live"
        try:
            meeting_run = store.load_meeting_run(default_mid)
        except (OSError, ValueError, StoreError):
            meeting_run = MeetingRun.create(
                meeting_run_id=default_mid,
                trigger_text="Live dispatch test",
                user_id="u-phase18",
                channel_id="ch-phase18",
                thread_id="th-phase18",
                priority="P1",
            )
            store.save_meeting_run(meeting_run)

    # Phase 14 multi-bot session
    phase14 = run_phase14_multi_bot_pilot(root=root, mode="dry-run")
    session = phase14.session
    worker_tasks = phase14.worker_tasks

    loop = AutonomousDispatchLoop(
        root=root,
        client=client,
        dry_run=(mode == "dry-run"),
        max_rounds=max_rounds,
    )

    results: list[dict[str, object]] = []
    prior_statuses: tuple[KanbanCardStatus, ...] = ()
    final_converged = False

    for _ in range(1, max_rounds + 1):
        result = loop.run(
            meeting_run=meeting_run,
            session=session,
            worker_tasks=worker_tasks,
            prior_card_statuses=prior_statuses,
        )
        results.append(result.to_dict())
        prior_statuses = result.card_statuses
        if result.converged:
            final_converged = True
            break

    artifact_path = _write_dispatch_artifact(root, meeting_run.meeting_run_id, results)

    return {
        "ok": True,
        "pilot_id": AUTONOMOUS_DISPATCH_LOOP_ID,
        "mode": mode,
        "meeting_run_id": meeting_run.meeting_run_id,
        "rounds": len(results),
        "max_rounds": max_rounds,
        "converged": final_converged,
        "results": results,
        "artifact_path": str(artifact_path),
        "error": "",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _priority_to_urgency(priority: str) -> str:
    return {"P0": "critical", "P1": "high", "P2": "normal", "P3": "low"}.get(
        priority,
        "normal",
    )


def _write_dispatch_artifact(
    root: Path,
    meeting_run_id: str,
    results: list[dict[str, object]],
) -> Path:
    path = root / "runtime" / "phase18-dispatch" / f"{meeting_run_id}.json"
    payload = {
        "meeting_run_id": meeting_run_id,
        "loop_id": AUTONOMOUS_DISPATCH_LOOP_ID,
        "rounds": len(results),
        "results": results,
    }
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
    )
    return path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
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
