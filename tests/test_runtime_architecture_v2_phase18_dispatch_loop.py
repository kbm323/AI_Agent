"""Phase 18 Live Kanban Autonomous Dispatch — TDD tests.

Covers: KanbanCardStatus, RecoveryAction, DispatchLoopResult,
AutonomousDispatchLoop (dry-run + live), recovery escalation,
Phase 17/18 integration, secret sanitization."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_architecture_v2.dispatch_loop import (
    AUTONOMOUS_DISPATCH_LOOP_ID,
    AutonomousDispatchLoop,
    DispatchLoopResult,
    KanbanCardStatus,
    RecoveryAction,
    run_phase18_autonomous_dispatch,
)
from runtime_architecture_v2.kanban_ops import (
    KanbanCardSpec,
    KanbanDispatchResult,
    KanbanOperationPlan,
)
from runtime_architecture_v2.multi_bot import MultiBotSession
from runtime_architecture_v2.scheduling_policy import (
    SchedulingDecision,
    SchedulingKind,
)
from runtime_architecture_v2.schemas import (
    MeetingRun,
    MeetingRunState,
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)

_UTC = UTC


def _meeting_run(
    meeting_run_id: str = "mr-18-test-001", state=MeetingRunState.ACTIVE
) -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id=meeting_run_id,
        trigger_text="팬 참여 쇼츠 데뷔 전략",
        user_id="u-test",
        channel_id="ch-test",
        thread_id="th-test",
        priority="P1",
    )


def _worker_tasks(meeting_run_id: str = "mr-18-test-001") -> tuple[WorkerTask, ...]:
    roles = ("content_lead", "art_director", "tech_lead")
    return tuple(
        WorkerTask(
            worker_task_id=f"wt_{meeting_run_id}_{i}_{role}",
            meeting_run_id=meeting_run_id,
            role=role,
            runner=WorkerTaskRunner.OPENCODE_GO,
            state=WorkerTaskState.CREATED,
        )
        for i, role in enumerate(roles, start=1)
    )


def _session(meeting_run_id: str = "mr-18-test-001") -> MultiBotSession:
    return MultiBotSession(
        meeting_run_id=meeting_run_id,
        title="테스트 세션",
        bot_roles=("content_lead", "art_director", "tech_lead"),
        consensus_summary="테스트 요약",
    )


def _dispatch_result(
    card_ids: tuple[str, ...],
    dry_run: bool = True,
) -> KanbanDispatchResult:
    refs = tuple(f"dry_run:{cid}" if dry_run else f"card_ref:{cid}" for cid in card_ids)
    return KanbanDispatchResult(
        ok=True,
        meeting_run_id="mr-18-test-001",
        dry_run=dry_run,
        created_refs=refs,
        local_to_remote_refs=dict(zip(card_ids, refs, strict=False)),
    )


# ---------------------------------------------------------------------------
# KanbanCardStatus
# ---------------------------------------------------------------------------


class TestKanbanCardStatusSchema:
    def test_card_status_fields(self) -> None:
        now = datetime.now(_UTC)
        status = KanbanCardStatus(
            card_id="kb_mr-18_1_content_lead",
            meeting_run_id="mr-18-test-001",
            kind="worker",
            state="claimed",
            claimed_by="content_lead",
            completed_at=now,
            blocked_reason="",
            reclaim_count=0,
            age_hours=2.5,
        )
        assert status.card_id == "kb_mr-18_1_content_lead"
        assert status.state == "claimed"
        assert status.reclaim_count == 0

    def test_card_status_to_dict(self) -> None:
        now = datetime.now(_UTC)
        status = KanbanCardStatus(
            card_id="kb_mr-18_1",
            meeting_run_id="mr-18-test-001",
            kind="worker",
            state="completed",
            claimed_by="content_lead",
            completed_at=now,
            blocked_reason="",
            reclaim_count=1,
            age_hours=3.0,
        )
        d = status.to_dict()
        assert d["card_id"] == "kb_mr-18_1"
        assert d["state"] == "completed"
        assert d["completed_at"] is not None
        assert d["reclaim_count"] == 1

    def test_card_status_no_secret_leak(self) -> None:
        status = KanbanCardStatus(
            card_id="kb_mr_token_leak",
            meeting_run_id="mr-18-test-001",
            kind="worker",
            state="failed",
            claimed_by="",
            completed_at=None,
            blocked_reason="api_key=*** leaked",
            reclaim_count=0,
            age_hours=0.1,
        )
        d = status.to_dict()
        raw = json.dumps(d)
        assert "sk-abc123" not in raw
        assert "api_key" not in raw


# ---------------------------------------------------------------------------
# RecoveryAction
# ---------------------------------------------------------------------------


class TestRecoveryActionSchema:
    def test_recovery_action_fields(self) -> None:
        action = RecoveryAction(
            card_id="kb_mr-18_1",
            action="reassign",
            reason="card stuck for 4h",
            target_assignee="tech_lead",
        )
        assert action.action == "reassign"
        assert action.target_assignee == "tech_lead"

    def test_recovery_action_to_dict(self) -> None:
        action = RecoveryAction(
            card_id="kb_mr-18_2",
            action="escalate",
            reason="max reclaim attempts exceeded",
            target_assignee="orchestrator",
        )
        d = action.to_dict()
        assert d["action"] == "escalate"
        assert "max reclaim" in d["reason"]


# ---------------------------------------------------------------------------
# DispatchLoopResult
# ---------------------------------------------------------------------------


class TestDispatchLoopResultSchema:
    def test_result_fields(self) -> None:
        status = KanbanCardStatus(
            card_id="kb_mr-18_1",
            meeting_run_id="mr-18-test-001",
            kind="worker",
            state="completed",
            claimed_by="content_lead",
            completed_at=datetime.now(_UTC),
            blocked_reason="",
            reclaim_count=0,
            age_hours=1.0,
        )
        result = DispatchLoopResult(
            ok=True,
            dry_run=True,
            meeting_run_id="mr-18-test-001",
            round_number=1,
            dispatched_count=3,
            claimed_count=3,
            completed_count=3,
            blocked_count=0,
            failed_count=0,
            recovered_count=0,
            escalated_count=0,
            converged=True,
            card_statuses=(status,),
            recovery_actions=(),
            error="",
        )
        assert result.converged is True
        assert result.dispatched_count == 3

    def test_result_to_dict(self) -> None:
        status = KanbanCardStatus(
            card_id="kb_mr-18_1",
            meeting_run_id="mr-18-test-001",
            kind="worker",
            state="completed",
            claimed_by="content_lead",
            completed_at=datetime.now(_UTC),
            blocked_reason="",
            reclaim_count=0,
            age_hours=1.0,
        )
        result = DispatchLoopResult(
            ok=True,
            dry_run=True,
            meeting_run_id="mr-18-test-001",
            round_number=1,
            dispatched_count=1,
            claimed_count=1,
            completed_count=1,
            blocked_count=0,
            failed_count=0,
            recovered_count=0,
            escalated_count=0,
            converged=True,
            card_statuses=(status,),
            recovery_actions=(),
            error="",
        )
        d = result.to_dict()
        assert d["ok"] is True
        assert len(d["card_statuses"]) == 1
        assert d["converged"] is True


# ---------------------------------------------------------------------------
# AutonomousDispatchLoop — dry-run
# ---------------------------------------------------------------------------


class TestAutonomousDispatchLoopDryRun:
    def test_dry_run_all_cards_complete(self, tmp_path: Path) -> None:
        loop = AutonomousDispatchLoop(
            root=tmp_path,
            client=None,
            dry_run=True,
            max_rounds=3,
            max_reclaim_attempts=2,
        )
        plan = KanbanOperationPlan(
            ok=True,
            meeting_run_id="mr-18-test-001",
            cards=(),
            scheduling_decision=SchedulingDecision(
                meeting_run_id="mr-18-test-001",
                kind=SchedulingKind.HERMES_KANBAN,
                hermes_primitive="kanban_card",
                reason="test",
            ),
            priority_decision=MagicMock(),
            concurrency_limits={},
        )
        statuses = (
            KanbanCardStatus(
                "kb_mr-18_1",
                "mr-18-test-001",
                "worker",
                "completed",
                "bot",
                datetime.now(_UTC),
                "",
                0,
                1.0,
            ),
            KanbanCardStatus(
                "kb_mr-18_2",
                "mr-18-test-001",
                "review",
                "completed",
                "validator",
                datetime.now(_UTC),
                "",
                0,
                1.0,
            ),
        )
        dispatch = _dispatch_result(("kb_mr-18_1", "kb_mr-18_2"))
        result = loop._build_result(
            plan=plan,
            dispatch=dispatch,
            card_statuses=statuses,
            recovery_actions=(),
            round_number=1,
        )
        assert result.ok is True
        assert result.converged is True
        assert result.completed_count == 2
        assert result.failed_count == 0

    def test_dry_run_convergence_all_completed(self) -> None:
        loop = AutonomousDispatchLoop(root=Path("/tmp"), dry_run=True)
        result = loop._build_result(
            plan=KanbanOperationPlan(
                ok=True,
                meeting_run_id="mr-x",
                cards=(),
                scheduling_decision=SchedulingDecision(
                    meeting_run_id="mr-x",
                    kind=SchedulingKind.HERMES_KANBAN,
                    hermes_primitive="kanban_card",
                    reason="test",
                ),
                priority_decision=MagicMock(),
                concurrency_limits={},
            ),
            dispatch=_dispatch_result(()),
            card_statuses=(),
            recovery_actions=(),
            round_number=1,
        )
        assert result.converged is True

    def test_dry_run_not_converged_when_blocked(self) -> None:
        loop = AutonomousDispatchLoop(root=Path("/tmp"), dry_run=True)
        status = KanbanCardStatus(
            "kb_mr_blocked",
            "mr-x",
            "worker",
            "blocked",
            "bot",
            None,
            "rate limit",
            0,
            2.0,
        )
        result = loop._build_result(
            plan=KanbanOperationPlan(
                ok=True,
                meeting_run_id="mr-x",
                cards=(),
                scheduling_decision=SchedulingDecision(
                    meeting_run_id="mr-x",
                    kind=SchedulingKind.HERMES_KANBAN,
                    hermes_primitive="kanban_card",
                    reason="test",
                ),
                priority_decision=MagicMock(),
                concurrency_limits={},
            ),
            dispatch=_dispatch_result(("kb_mr_blocked",)),
            card_statuses=(status,),
            recovery_actions=(),
            round_number=1,
        )
        assert result.converged is False
        assert result.blocked_count == 1
        assert result.ok is False  # dispatch ok but cards blocked → fail

    def test_dry_run_ok_false_when_cards_failed(self) -> None:
        loop = AutonomousDispatchLoop(root=Path("/tmp"), dry_run=True)
        failed_status = KanbanCardStatus(
            "kb_mr_failed",
            "mr-x",
            "worker",
            "failed",
            "bot",
            None,
            "crash",
            0,
            0.5,
        )
        result = loop._build_result(
            plan=KanbanOperationPlan(
                ok=True,
                meeting_run_id="mr-x",
                cards=(),
                scheduling_decision=SchedulingDecision(
                    meeting_run_id="mr-x",
                    kind=SchedulingKind.HERMES_KANBAN,
                    hermes_primitive="kanban_card",
                    reason="test",
                ),
                priority_decision=MagicMock(),
                concurrency_limits={},
            ),
            dispatch=_dispatch_result(("kb_mr_failed",)),
            card_statuses=(failed_status,),
            recovery_actions=(),
            round_number=1,
        )
        assert result.ok is False
        assert result.failed_count == 1


# ---------------------------------------------------------------------------
# AutonomousDispatchLoop — live mode
# ---------------------------------------------------------------------------


class FakeKanbanClient:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.created_cards: list[dict] = []
        self.should_fail = should_fail

    def create_card(
        self,
        *,
        title: str,
        body: str,
        assignee: str,
        priority: str,
        parents: list[str],
        metadata: dict,
    ) -> str:
        if self.should_fail:
            raise RuntimeError("fake client failure")
        ref = f"card_ref:{len(self.created_cards)}"
        self.created_cards.append(
            {
                "title": title,
                "assignee": assignee,
                "priority": priority,
                "parents": parents,
                "ref": ref,
            }
        )
        return ref


class TestAutonomousDispatchLoopLive:
    def test_live_dispatch_success(self, tmp_path: Path) -> None:
        client = FakeKanbanClient()
        loop = AutonomousDispatchLoop(
            root=tmp_path,
            client=client,
            dry_run=False,
            max_rounds=3,
            max_reclaim_attempts=2,
        )
        dispatch_result = loop._apply_dispatch(
            plan=KanbanOperationPlan(
                ok=True,
                meeting_run_id="mr-18-live",
                cards=(
                    KanbanCardSpec(
                        card_id="kb_live_1",
                        meeting_run_id="mr-18-live",
                        kind="worker",
                        title="task 1",
                        body="body 1",
                        assignee="bot_a",
                        priority="P1",
                        parents=(),
                        metadata={},
                    ),
                ),
                scheduling_decision=SchedulingDecision(
                    meeting_run_id="mr-18-live",
                    kind=SchedulingKind.HERMES_KANBAN,
                    hermes_primitive="kanban_card",
                    reason="test",
                ),
                priority_decision=MagicMock(),
                concurrency_limits={},
            ),
        )
        assert dispatch_result.ok is True
        assert len(dispatch_result.created_refs) == 1
        assert len(client.created_cards) == 1

    def test_live_dispatch_no_client(self, tmp_path: Path) -> None:
        loop = AutonomousDispatchLoop(
            root=tmp_path,
            client=None,
            dry_run=False,
        )
        dispatch_result = loop._apply_dispatch(
            plan=KanbanOperationPlan(
                ok=True,
                meeting_run_id="mr-18-noclient",
                cards=(),
                scheduling_decision=SchedulingDecision(
                    meeting_run_id="mr-18-noclient",
                    kind=SchedulingKind.HERMES_KANBAN,
                    hermes_primitive="kanban_card",
                    reason="test",
                ),
                priority_decision=MagicMock(),
                concurrency_limits={},
            ),
        )
        assert dispatch_result.ok is False
        assert dispatch_result.error == "kanban_client_required"

    def test_live_dispatch_client_failure(self, tmp_path: Path) -> None:
        client = FakeKanbanClient(should_fail=True)
        loop = AutonomousDispatchLoop(
            root=tmp_path,
            client=client,
            dry_run=False,
        )
        dispatch_result = loop._apply_dispatch(
            plan=KanbanOperationPlan(
                ok=True,
                meeting_run_id="mr-18-fail",
                cards=(
                    KanbanCardSpec(
                        card_id="kb_fail_1",
                        meeting_run_id="mr-18-fail",
                        kind="worker",
                        title="t",
                        body="b",
                        assignee="bot",
                        priority="P2",
                        parents=(),
                        metadata={},
                    ),
                ),
                scheduling_decision=SchedulingDecision(
                    meeting_run_id="mr-18-fail",
                    kind=SchedulingKind.HERMES_KANBAN,
                    hermes_primitive="kanban_card",
                    reason="test",
                ),
                priority_decision=MagicMock(),
                concurrency_limits={},
            ),
        )
        assert dispatch_result.ok is False
        assert "kanban_dispatch_failed" in dispatch_result.error


# ---------------------------------------------------------------------------
# Recovery — escalation
# ---------------------------------------------------------------------------


class TestRecoveryEscalation:
    def test_exceeding_max_reclaim_escalates(self, tmp_path: Path) -> None:
        loop = AutonomousDispatchLoop(
            root=tmp_path,
            dry_run=True,
            max_reclaim_attempts=2,
        )
        actions = loop._recover_cards(
            card_statuses=(
                KanbanCardStatus(
                    "kb_escalate",
                    "mr-x",
                    "worker",
                    "blocked",
                    "bot",
                    None,
                    "stuck",
                    reclaim_count=2,
                    age_hours=5.0,
                ),
            ),
            round_number=1,
        )
        assert len(actions) == 1
        assert actions[0].action == "escalate"
        assert "max reclaim" in actions[0].reason.lower()

    def test_blocked_card_within_reclaim_retries(self, tmp_path: Path) -> None:
        loop = AutonomousDispatchLoop(
            root=tmp_path,
            dry_run=True,
            max_reclaim_attempts=3,
        )
        actions = loop._recover_cards(
            card_statuses=(
                KanbanCardStatus(
                    "kb_retry",
                    "mr-x",
                    "worker",
                    "blocked",
                    "bot",
                    None,
                    "rate limit",
                    reclaim_count=1,
                    age_hours=2.0,
                ),
            ),
            round_number=1,
        )
        assert len(actions) == 1
        assert actions[0].action == "reassign"
        assert actions[0].target_assignee == "bot"

    def test_no_recovery_for_completed_cards(self, tmp_path: Path) -> None:
        loop = AutonomousDispatchLoop(root=tmp_path, dry_run=True)
        actions = loop._recover_cards(
            card_statuses=(
                KanbanCardStatus(
                    "kb_done",
                    "mr-x",
                    "worker",
                    "completed",
                    "bot",
                    datetime.now(_UTC),
                    "",
                    0,
                    1.0,
                ),
            ),
            round_number=1,
        )
        assert len(actions) == 0


# ---------------------------------------------------------------------------
# run_phase18_autonomous_dispatch (CLI pilot)
# ---------------------------------------------------------------------------


class TestPhase18CLIPilot:
    def test_dry_run_mode(self, tmp_path: Path) -> None:
        result = run_phase18_autonomous_dispatch(
            root=tmp_path,
            mode="dry-run",
            meeting_run_id="mr-18-cli",
            max_rounds=2,
        )
        assert result["ok"] is True
        assert result["mode"] == "dry-run"
        assert result["pilot_id"] == AUTONOMOUS_DISPATCH_LOOP_ID
        assert "rounds" in result
        assert "converged" in result

    def test_live_mode_requires_client(self, tmp_path: Path) -> None:
        result = run_phase18_autonomous_dispatch(
            root=tmp_path,
            mode="live",
            meeting_run_id="mr-18-cli",
            max_rounds=2,
        )
        assert result["ok"] is False
        assert "kanban_client_required" in result.get("error", "")

    def test_invalid_mode(self, tmp_path: Path) -> None:
        result = run_phase18_autonomous_dispatch(
            root=tmp_path,
            mode="chaos",
            meeting_run_id="mr-18-cli",
        )
        assert result["ok"] is False
        assert "unsupported" in result.get("error", "").lower()

    def test_artifact_written(self, tmp_path: Path) -> None:
        _result = run_phase18_autonomous_dispatch(
            root=tmp_path,
            mode="dry-run",
            meeting_run_id="mr-18-artifact",
            max_rounds=1,
        )
        artifact_dir = tmp_path / "runtime" / "phase18-dispatch"
        assert artifact_dir.exists()
        artifacts = list(artifact_dir.glob("*.json"))
        assert len(artifacts) >= 1


# ---------------------------------------------------------------------------
# Boundary: no secret/token/misuse leaks
# ---------------------------------------------------------------------------


class TestPhase18BoundarySafety:
    def test_card_status_rejects_token_in_blocked_reason(self) -> None:
        status = KanbanCardStatus(
            card_id="kb_leak",
            meeting_run_id="mr-leak",
            kind="worker",
            state="blocked",
            claimed_by="bot",
            completed_at=None,
            blocked_reason="Error: api_secret=supersecret (blocked)",
            reclaim_count=0,
            age_hours=0.2,
        )
        d = status.to_dict()
        raw = json.dumps(d)
        assert "supersecret" not in raw
        assert "api_secret" not in raw

    def test_card_status_rejects_bearer_token(self) -> None:
        status = KanbanCardStatus(
            card_id="kb_bearer",
            meeting_run_id="mr-bearer",
            kind="worker",
            state="failed",
            claimed_by="",
            completed_at=None,
            blocked_reason="Bearer tok_deadbeef123456 expired",
            reclaim_count=0,
            age_hours=0.5,
        )
        d = status.to_dict()
        raw = json.dumps(d)
        assert "tok_deadbeef" not in raw
        assert "Bearer" not in raw
