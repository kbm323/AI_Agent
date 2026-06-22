from __future__ import annotations

import json

from src.runtime_architecture_v2.orchestrator import RuntimeOrchestrator
from src.runtime_architecture_v2.schemas import MeetingRunState, WorkerTaskState
from src.runtime_architecture_v2.workers import FakeWorkerRunner, WorkerRunError


class DispatchExplodingRunner(FakeWorkerRunner):
    def dispatch(self, task):  # noqa: ANN001
        raise WorkerRunError(
            code="dispatch_failed",
            message="simulated dispatch exception",
            worker_task_id=task.worker_task_id,
        )


def test_orchestrator_runs_full_fake_meeting_flow_to_projection(tmp_path):
    orchestrator = RuntimeOrchestrator(root=tmp_path, worker_runner=FakeWorkerRunner())

    result = orchestrator.run(
        meeting_run_id="mr_phase7",
        trigger_text="콘셉트 기획과 코드 구현, 마케팅 전략까지 같이 회의해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        guild_id="guild-1",
        hermes_session_id="sess-1",
        simulation=True,
    )

    assert result.meeting_run.meeting_run_id == "mr_phase7"
    assert result.meeting_run.state == MeetingRunState.COMPLETED
    assert result.routing_result.route_type == "mixed_request"
    assert result.scheduling_decision.kind == "local_fake"
    assert result.scheduling_decision.requires_custom_queue_store is False
    assert result.validation_decision.next_state == "reporting"
    assert all(task.state == WorkerTaskState.SUCCEEDED for task in result.worker_tasks)
    assert len(result.worker_tasks) == 3
    assert len(result.validation_verdicts) == 2
    assert all(verdict.verdict == "pass" for verdict in result.validation_verdicts)
    assert result.projection_event.source == "meeting_run"
    assert result.projection_event.source_id == "mr_phase7"
    assert result.projection_publish_result.status == "published"
    assert "raw_worker_outputs" in result.projection_event.content
    assert "\"answer\": \"ok\"" not in result.projection_event.content
    assert result.checkpoint.state == MeetingRunState.COMPLETED
    assert result.checkpoint.idempotency_key == "mr_phase7:completed"

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_phase7"
    assert (run_dir / "meeting_run.json").exists()
    assert (run_dir / "decision_log.jsonl").exists()
    assert (run_dir / "audit_log.jsonl").exists()
    assert (run_dir / "discord_projection" / "proj_mr_phase7.json").exists()

    restored = json.loads((run_dir / "meeting_run.json").read_text(encoding="utf-8"))
    assert restored["state"] == "completed"
    assert restored["projection_event_ids"] == ["proj_mr_phase7"]
    assert restored["routing_result"]["route_type"] == "mixed_request"


def test_orchestrator_fails_closed_when_worker_fails(tmp_path):
    orchestrator = RuntimeOrchestrator(
        root=tmp_path,
        worker_runner=FakeWorkerRunner(fail_with="simulated worker failure"),
    )

    result = orchestrator.run(
        meeting_run_id="mr_worker_fail",
        trigger_text="코드 구현과 테스트 실행해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=True,
    )

    assert result.meeting_run.state == MeetingRunState.FAILED
    assert result.validation_decision.next_state == "failed"
    assert result.validation_decision.kind == "stop"
    assert all(task.state == WorkerTaskState.FAILED for task in result.worker_tasks)
    assert any(verdict.verdict == "reject" for verdict in result.validation_verdicts)
    assert result.projection_event.bot_role == "validation_audit"
    assert result.projection_publish_result.status == "published"
    assert "simulated worker failure" in result.projection_event.content
    assert "\"answer\": \"ok\"" not in result.projection_event.content

    audit_log = (
        tmp_path
        / "runtime"
        / "meeting_runs"
        / "mr_worker_fail"
        / "audit_log.jsonl"
    ).read_text(encoding="utf-8")
    assert "worker_failed" in audit_log
    assert "simulated worker failure" in audit_log


def test_orchestrator_fails_closed_when_worker_runner_raises(tmp_path):
    orchestrator = RuntimeOrchestrator(
        root=tmp_path,
        worker_runner=DispatchExplodingRunner(),
    )

    result = orchestrator.run(
        meeting_run_id="mr_dispatch_error",
        trigger_text="코드 구현과 테스트 실행해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=True,
    )

    assert result.meeting_run.state == MeetingRunState.FAILED
    assert result.validation_decision.kind == "stop"
    assert result.worker_tasks[0].state == WorkerTaskState.FAILED
    assert "simulated dispatch exception" in result.worker_tasks[0].error
    assert any(verdict.verdict == "reject" for verdict in result.validation_verdicts)
    assert result.projection_event.bot_role == "validation_audit"
    assert result.projection_publish_result.status == "published"

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_dispatch_error"
    assert json.loads((run_dir / "meeting_run.json").read_text())["state"] == "failed"
    audit_log = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    assert "worker_failed" in audit_log
    assert "dispatch_failed" in audit_log


def test_orchestrator_non_simulation_uses_hermes_native_scheduling(tmp_path):
    orchestrator = RuntimeOrchestrator(root=tmp_path, worker_runner=FakeWorkerRunner())

    result = orchestrator.run(
        meeting_run_id="mr_hermes_schedule",
        trigger_text="코드 구현해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=False,
    )

    assert result.scheduling_decision.kind == "hermes_background_process"
    assert result.scheduling_decision.hermes_primitive == "background_process"
    assert result.scheduling_decision.requires_custom_queue_store is False
