from __future__ import annotations

import pytest

from src.runtime_architecture_v2.schemas import (
    DiscordProjectionEvent,
    MeetingRun,
    MeetingRunState,
    RecoveryCheckpoint,
    RoutingResult,
    ValidationVerdict,
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)


def test_meeting_run_serializes_domain_source_of_truth_without_hermes_state():
    run = MeetingRun.create(
        meeting_run_id="mr_001",
        trigger_text="긴급 기술 회의 열어줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        guild_id="guild-1",
        hermes_session_id="sess-1",
        priority="P1",
    )

    payload = run.to_dict()

    assert payload["meeting_run_id"] == "mr_001"
    assert payload["state"] == "created"
    assert payload["priority"] == "P1"
    assert payload["trigger"]["text"] == "긴급 기술 회의 열어줘"
    assert payload["trigger"]["discord"]["thread_id"] == "thread-1"
    assert payload["hermes_refs"] == {"session_id": "sess-1"}
    assert "hermes_memory" not in payload
    assert "discord_history" not in payload

    restored = MeetingRun.from_dict(payload)
    assert restored == run
    assert restored.is_terminal is False


def test_meeting_run_rejects_invalid_state_and_priority():
    with pytest.raises(ValueError, match="invalid MeetingRun state"):
        MeetingRun(
            meeting_run_id="mr_bad",
            state="waiting",  # type: ignore[arg-type]
            trigger={"text": "x"},
        )

    with pytest.raises(ValueError, match="invalid priority"):
        MeetingRun.create(
            meeting_run_id="mr_bad_priority",
            trigger_text="x",
            user_id="u",
            channel_id="c",
            thread_id="t",
            priority="urgent",
        )


def test_routing_result_forbids_openclaw_and_keeps_research_delegated_to_teams():
    result = RoutingResult(
        meeting_run_id="mr_001",
        route_type="meeting",
        teams=("tech_lead", "marketing_lead"),
        worker_roles=("pipeline_rd",),
        validators=("glm_validator", "codex_auditor"),
        research_owner="tech_lead",
        execution_required=True,
        projection_policy="summary_only",
    )

    payload = result.to_dict()

    assert payload["teams"] == ["tech_lead", "marketing_lead"]
    assert payload["research_owner"] == "tech_lead"
    assert payload["validators"] == ["glm_validator", "codex_auditor"]
    assert "openclaw_required" not in payload

    with pytest.raises(ValueError, match="Research must be delegated"):
        RoutingResult(
            meeting_run_id="mr_002",
            route_type="meeting",
            teams=("research_lead",),
            research_owner="research_lead",
        )


def test_worker_task_runner_is_opencode_go_or_hermes_wrapper_only():
    task = WorkerTask(
        worker_task_id="wt_001",
        meeting_run_id="mr_001",
        role="glm_validator",
        runner=WorkerTaskRunner.OPENCODE_GO,
        state=WorkerTaskState.CREATED,
        model_policy={"preferred": "glm-5.1", "role": "validator"},
        packet_path="runtime/meeting_runs/mr_001/packets/wt_001.json",
    )

    payload = task.to_dict()

    assert payload["runner"] == "opencode_go"
    assert payload["model_policy"]["role"] == "validator"
    assert WorkerTask.from_dict(payload) == task

    with pytest.raises(ValueError, match="invalid WorkerTask runner"):
        WorkerTask(
            worker_task_id="wt_002",
            meeting_run_id="mr_001",
            role="external_executor",
            runner="openclaw",  # type: ignore[arg-type]
        )


def test_glm_validator_and_codex_auditor_are_opencode_go_roles():
    glm_task = WorkerTask(
        worker_task_id="wt_glm",
        meeting_run_id="mr_001",
        role="glm_validator",
        runner=WorkerTaskRunner.OPENCODE_GO,
        model_policy={
            "preferred": "glm-5.1",
            "execution_role": "validator",
            "model_family": "glm",
        },
    )
    codex_task = WorkerTask(
        worker_task_id="wt_codex",
        meeting_run_id="mr_001",
        role="codex_auditor",
        runner=WorkerTaskRunner.OPENCODE_GO,
        model_policy={
            "preferred": "codex",
            "execution_role": "auditor",
            "model_family": "codex",
            "fallback_runner": "codex_cli",
        },
    )

    assert glm_task.to_dict()["model_policy"]["execution_role"] == "validator"
    assert codex_task.to_dict()["model_policy"]["execution_role"] == "auditor"
    assert glm_task.to_dict()["runner"] == "opencode_go"
    assert codex_task.to_dict()["runner"] == "opencode_go"


def test_validation_verdict_and_projection_event_have_explicit_roles():
    verdict = ValidationVerdict(
        validation_id="val_001",
        meeting_run_id="mr_001",
        validator_role="codex_auditor",
        validator_model="codex",
        verdict="revise",
        confidence=0.72,
        findings=("missing recovery checkpoint",),
        required_actions=("add checkpoint schema",),
    )
    event = DiscordProjectionEvent(
        event_id="proj_001",
        meeting_run_id="mr_001",
        bot_role="validation_audit",
        target_channel_id="results-1",
        content="검증 결과: revise",
        source="validation_verdict",
        source_id="val_001",
    )

    assert verdict.to_dict()["validator_role"] == "codex_auditor"
    assert verdict.to_dict()["findings"] == ["missing recovery checkpoint"]
    assert event.to_dict()["bot_role"] == "validation_audit"
    assert DiscordProjectionEvent.from_dict(event.to_dict()) == event

    with pytest.raises(ValueError, match="confidence"):
        ValidationVerdict(
            validation_id="val_bad",
            meeting_run_id="mr_001",
            validator_role="glm_validator",
            validator_model="glm",
            verdict="pass",
            confidence=1.5,
        )


def test_recovery_checkpoint_references_hermes_native_execution_without_queue_db():
    checkpoint = RecoveryCheckpoint(
        checkpoint_id="chk_001",
        meeting_run_id="mr_001",
        state=MeetingRunState.ACTIVE,
        completed_worker_task_ids=("wt_001",),
        pending_worker_task_ids=("wt_002",),
        hermes_refs={"background_process_id": "proc-1", "kanban_card_id": "card-1"},
        checkpoint_path="runtime/meeting_runs/mr_001/checkpoints/chk_001.json",
        idempotency_key="mr_001:active:wt_001",
        replay_token="replay-001",
    )

    payload = checkpoint.to_dict()

    assert payload["state"] == "active"
    assert payload["hermes_refs"]["background_process_id"] == "proc-1"
    assert payload["idempotency_key"] == "mr_001:active:wt_001"
    assert payload["replay_token"] == "replay-001"
    assert "queue_db" not in payload
    assert RecoveryCheckpoint.from_dict(payload) == checkpoint


def test_all_phase_1_schemas_round_trip():
    routing = RoutingResult(
        meeting_run_id="mr_001",
        route_type="worker_execution",
        teams=("tech_lead",),
        worker_roles=("pipeline_rd",),
        validators=("glm_validator",),
        research_owner="tech_lead",
        confidence=0.93,
    )
    verdict = ValidationVerdict(
        validation_id="val_001",
        meeting_run_id="mr_001",
        validator_role="glm_validator",
        validator_model="glm-5.1",
        verdict="pass",
        confidence=0.91,
        findings=("consistent",),
    )

    assert RoutingResult.from_dict(routing.to_dict()) == routing
    assert ValidationVerdict.from_dict(verdict.to_dict()) == verdict
