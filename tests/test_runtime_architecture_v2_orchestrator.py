from __future__ import annotations

import json

from src.runtime_architecture_v2.orchestrator import RuntimeOrchestrator
from src.runtime_architecture_v2.policies import (
    QuotaPolicy,
    QuotaSnapshot,
    SecurityPolicy,
)
from src.runtime_architecture_v2.schemas import MeetingRunState, WorkerTaskState
from src.runtime_architecture_v2.workers import FakeWorkerRunner, WorkerRunError


class DispatchExplodingRunner(FakeWorkerRunner):
    def dispatch(self, task):  # noqa: ANN001
        raise WorkerRunError(
            code="dispatch_failed",
            message="token=supersecret simulated dispatch exception",
            worker_task_id=task.worker_task_id,
        )


class SpyWorkerRunner(FakeWorkerRunner):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_count = 0

    def dispatch(self, task):  # noqa: ANN001
        self.dispatch_count += 1
        return super().dispatch(task)


class ExplodingSecurityPolicy(SecurityPolicy):
    def evaluate(self, run):  # noqa: ANN001
        del run
        raise RuntimeError("simulated security policy failure")


class ExplodingQuotaPolicy(QuotaPolicy):
    def evaluate(self, *, active_provider: str):
        del active_provider
        raise RuntimeError("simulated quota policy failure")


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
    assert result.worker_tasks[0].error == "dispatch_failed"
    assert "supersecret" not in result.projection_event.content
    assert "simulated dispatch exception" not in result.projection_event.content
    assert any(verdict.verdict == "reject" for verdict in result.validation_verdicts)
    assert result.projection_event.bot_role == "validation_audit"
    assert result.projection_publish_result.status == "published"

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_dispatch_error"
    assert json.loads((run_dir / "meeting_run.json").read_text())["state"] == "failed"
    audit_log = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    assert "worker_failed" in audit_log
    assert "dispatch_failed" in audit_log
    assert "supersecret" not in audit_log
    assert "simulated dispatch exception" not in audit_log


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


def test_orchestrator_security_gate_pauses_before_routing_or_workers(tmp_path):
    runner = SpyWorkerRunner()
    orchestrator = RuntimeOrchestrator(
        root=tmp_path,
        worker_runner=runner,
        security_policy=SecurityPolicy(),
    )

    result = orchestrator.run(
        meeting_run_id="mr_security_block",
        trigger_text="API_TOKEN=example-secret-value 포함 회의",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=True,
    )

    assert result.meeting_run.state == MeetingRunState.PAUSED
    assert result.security_decision.allowed is False
    assert result.security_decision.reason == "secret_like_input_detected"
    assert result.worker_tasks == ()
    assert runner.dispatch_count == 0
    assert result.routing_result.route_type == "policy_blocked"
    assert result.projection_event.bot_role == "validation_audit"
    assert "example-secret-value" not in result.projection_event.content
    assert "[REDACTED]" in result.projection_event.content

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_security_block"
    audit_log = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    assert "security_gate" in audit_log
    assert "observability_event" in audit_log
    assert "example-secret-value" not in audit_log


def test_orchestrator_quota_gate_pauses_before_worker_dispatch(tmp_path):
    runner = SpyWorkerRunner()
    orchestrator = RuntimeOrchestrator(
        root=tmp_path,
        worker_runner=runner,
        quota_policy=QuotaPolicy(
            snapshot=QuotaSnapshot(
                provider="codex",
                monthly_percent=0,
                weekly_percent=99,
                hourly_percent=12,
            )
        ),
        active_provider="codex",
    )

    result = orchestrator.run(
        meeting_run_id="mr_quota_pause",
        trigger_text="코드 구현해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=False,
    )

    assert result.meeting_run.state == MeetingRunState.PAUSED
    assert result.quota_decision.allowed is False
    assert result.quota_decision.reason == "quota_weekly_critical"
    assert result.worker_tasks == ()
    assert runner.dispatch_count == 0
    assert result.scheduling_decision.kind == "hermes_background_process"
    assert result.checkpoint.state == MeetingRunState.PAUSED

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_quota_pause"
    audit_log = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    assert "quota_gate" in audit_log
    assert "weekly 99%" in audit_log


def test_orchestrator_security_policy_exception_fails_closed_without_raw_persist(
    tmp_path,
):
    orchestrator = RuntimeOrchestrator(
        root=tmp_path,
        worker_runner=SpyWorkerRunner(),
        security_policy=ExplodingSecurityPolicy(),
    )

    result = orchestrator.run(
        meeting_run_id="mr_security_exception",
        trigger_text="API_TOKEN=example-secret-value 포함 회의",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=True,
    )

    assert result.meeting_run.state == MeetingRunState.PAUSED
    assert result.security_decision.allowed is False
    assert result.security_decision.reason == "security_policy_exception"
    assert result.worker_tasks == ()

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_security_exception"
    persisted = (run_dir / "meeting_run.json").read_text(encoding="utf-8")
    audit_log = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    projection = (
        run_dir / "discord_projection" / "proj_mr_security_exception.json"
    ).read_text(encoding="utf-8")
    assert "example-secret-value" not in persisted
    assert "example-secret-value" not in audit_log
    assert "example-secret-value" not in projection


def test_orchestrator_quota_policy_exception_fails_closed_before_dispatch(tmp_path):
    runner = SpyWorkerRunner()
    orchestrator = RuntimeOrchestrator(
        root=tmp_path,
        worker_runner=runner,
        quota_policy=ExplodingQuotaPolicy(),
    )

    result = orchestrator.run(
        meeting_run_id="mr_quota_exception",
        trigger_text="코드 구현해줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        simulation=False,
    )

    assert result.meeting_run.state == MeetingRunState.PAUSED
    assert result.quota_decision.allowed is False
    assert result.quota_decision.reason == "quota_policy_exception"
    assert result.worker_tasks == ()
    assert runner.dispatch_count == 0

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_quota_exception"
    audit_log = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    assert "quota_gate" in audit_log
    assert "quota_policy_exception" in audit_log

