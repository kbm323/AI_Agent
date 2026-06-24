from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.runtime_architecture_v2.multi_bot import MultiBotSession
from src.runtime_architecture_v2.schemas import MeetingRun, WorkerTask, WorkerTaskRunner


def _meeting_run() -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id="mr_phase16_test",
        trigger_text="신규 버추얼 아이돌 데뷔 실행 과제를 Kanban으로 운영해줘",
        user_id="u1",
        channel_id="c1",
        thread_id="t1",
        priority="P2",
    )


def _session() -> MultiBotSession:
    return MultiBotSession(
        meeting_run_id="mr_phase16_test",
        participants=("content_lead", "marketing_lead", "quality_lead"),
        rounds=(),
        consensus_reached=True,
        escalation_required=False,
        consensus_summary="팬 참여형 쇼츠 데뷔 실행안을 각 팀장별 작업으로 분해한다.",
    )


def _worker_tasks() -> tuple[WorkerTask, ...]:
    return (
        WorkerTask(
            worker_task_id="wt_mr_phase16_test_1_content",
            meeting_run_id="mr_phase16_test",
            role="content_lead",
            runner=WorkerTaskRunner.HERMES_WRAPPER,
        ),
        WorkerTask(
            worker_task_id="wt_mr_phase16_test_2_marketing",
            meeting_run_id="mr_phase16_test",
            role="marketing_lead",
            runner=WorkerTaskRunner.HERMES_WRAPPER,
        ),
        WorkerTask(
            worker_task_id="wt_mr_phase16_test_3_quality",
            meeting_run_id="mr_phase16_test",
            role="quality_lead",
            runner=WorkerTaskRunner.HERMES_WRAPPER,
        ),
    )


def test_phase16_builds_hermes_native_kanban_plan_with_parallel_fanout(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import build_kanban_operation_plan

    plan = build_kanban_operation_plan(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        worker_tasks=_worker_tasks(),
        knowledge_context="## Prior Knowledge\n팬 참여형 쇼츠 전략",
    )

    assert plan.ok is True
    assert plan.meeting_run_id == "mr_phase16_test"
    assert plan.scheduling_decision.kind.value == "hermes_kanban"
    assert plan.requires_custom_queue_store is False
    assert plan.priority_decision.priority in {"P0", "P1", "P2", "P3"}

    worker_cards = [card for card in plan.cards if card.kind == "worker"]
    review_cards = [card for card in plan.cards if card.kind == "review"]
    assert len(worker_cards) == 3
    assert all(card.parents == () for card in worker_cards)
    assert len(review_cards) == 1
    assert set(review_cards[0].parents) == {card.card_id for card in worker_cards}
    assert all(card.metadata["queue_store"] == "none" for card in plan.cards)
    assert all(card.metadata["scheduling_primitive"] == "kanban" for card in plan.cards)
    assert all("priority_policy" in card.metadata for card in plan.cards)
    assert all("scheduling_kind" in card.metadata for card in plan.cards)
    assert all("role_concurrency_limit" in card.metadata for card in plan.cards)
    assert all("sanitization_rules" in card.metadata for card in plan.cards)


def test_phase16_kanban_card_bodies_include_sanitized_knowledge_context(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import build_kanban_operation_plan

    plan = build_kanban_operation_plan(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        worker_tasks=_worker_tasks(),
        knowledge_context=(
            "@everyone and @here use prior launch note with token=SHOULD_NOT_LEAK "
            "and Bearer ABCDEFG1234567"
        ),
    )
    combined = "\n".join(card.body for card in plan.cards)

    assert "@everyone" not in combined
    assert "@here" not in combined
    assert "SHOULD_NOT_LEAK" not in combined
    assert "ABCDEFG1234567" not in combined
    assert "@[redacted-mention]" in combined
    assert "[REDACTED_SECRET]" in combined
    assert "Prior knowledge" in combined


def test_phase16_rejects_mixed_meeting_run_worker_tasks(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import build_kanban_operation_plan

    wrong_task = WorkerTask(
        worker_task_id="wt_other_1",
        meeting_run_id="mr_other",
        role="content_lead",
        runner=WorkerTaskRunner.HERMES_WRAPPER,
    )

    with pytest.raises(ValueError, match="worker task meeting_run_id mismatch"):
        build_kanban_operation_plan(
            root=tmp_path,
            meeting_run=_meeting_run(),
            session=_session(),
            worker_tasks=(wrong_task,),
        )


def test_phase16_dispatch_dry_run_does_not_call_kanban_client(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import (
        build_kanban_operation_plan,
        dispatch_kanban_operation_plan,
    )

    class ExplodingClient:
        def create_card(self, **_kwargs: object) -> str:  # pragma: no cover
            raise AssertionError("dry-run must not call live kanban client")

    plan = build_kanban_operation_plan(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        worker_tasks=_worker_tasks(),
    )
    result = dispatch_kanban_operation_plan(
        plan, client=ExplodingClient(), dry_run=True
    )

    assert result.ok is True
    assert result.dry_run is True
    assert len(result.created_refs) == len(plan.cards)
    assert all(ref.startswith("dry_run:") for ref in result.created_refs)


def test_phase16_dispatch_with_injected_client_preserves_dependencies(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import (
        build_kanban_operation_plan,
        dispatch_kanban_operation_plan,
    )

    calls: list[dict[str, object]] = []

    class FakeKanbanClient:
        def create_card(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return f"kb_live_{len(calls)}"

    plan = build_kanban_operation_plan(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        worker_tasks=_worker_tasks(),
    )
    result = dispatch_kanban_operation_plan(
        plan, client=FakeKanbanClient(), dry_run=False
    )

    assert result.ok is True
    assert result.dry_run is False
    assert len(calls) == len(plan.cards)
    review_call = calls[-1]
    assert review_call["parents"] == ["kb_live_1", "kb_live_2", "kb_live_3"]
    assert review_call["metadata"]["queue_store"] == "none"


def test_phase16_dispatch_returns_sanitized_failure_for_client_errors(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import (
        build_kanban_operation_plan,
        dispatch_kanban_operation_plan,
    )

    class FailingKanbanClient:
        def create_card(self, **_kwargs: object) -> str:
            raise RuntimeError("token=SHOULD_NOT_LEAK")

    plan = build_kanban_operation_plan(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        worker_tasks=_worker_tasks(),
    )
    result = dispatch_kanban_operation_plan(
        plan, client=FailingKanbanClient(), dry_run=False
    )

    assert result.ok is False
    assert result.error == "kanban_dispatch_failed"
    assert "SHOULD_NOT_LEAK" not in str(result.to_dict()["error"])


def test_phase16_pilot_writes_plan_and_updates_meeting_metadata(tmp_path: Path):
    from src.runtime_architecture_v2.kanban_ops import run_phase16_kanban_pilot

    result = run_phase16_kanban_pilot(root=tmp_path, mode="dry-run")

    assert result["ok"] is True
    assert result["mode"] == "dry-run"
    assert result["pilot_id"] == "phase16_autonomous_scheduling_kanban_operations"
    assert result["kanban_card_count"] >= 4
    assert result["requires_custom_queue_store"] is False
    assert Path(result["plan_path"]).exists()
    assert result["dispatch_dry_run"] is True
    assert len(result["kanban_cards"]) == result["kanban_card_count"]
    assert result["kanban_cards"][-1]["kind"] == "review"


def test_phase16_cli_dry_run_outputs_machine_readable_json(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase16_kanban_pilot.py",
            "--mode",
            "dry-run",
            "--root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "dry-run"
    assert payload["kanban_card_count"] >= 4
    assert len(payload["kanban_cards"]) == payload["kanban_card_count"]
    assert payload["kanban_cards"][-1]["parents"]
    assert completed.stderr == ""
