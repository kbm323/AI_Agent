"""Phase 26 Hermes provider worker/validator/auditor boundary smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import (
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from src.runtime_architecture_v2.worker_boundary_smoke import (
    BoundarySmokeStatus,
    LiveWorkerBoundarySmokePolicy,
    sanitize_worker_output,
)
from src.runtime_architecture_v2.workers import (
    HermesProviderRunResult,
    HermesProviderWorkerRunner,
)


def _task(tmp_path: Path) -> WorkerTask:
    return WorkerTask(
        worker_task_id="wt_boundary_smoke",
        meeting_run_id="mr_boundary_smoke",
        role="glm_validator",
        runner=WorkerTaskRunner.HERMES_WRAPPER,
        packet_path=str(tmp_path / "packets" / "wt_boundary_smoke.json"),
        output_path=str(tmp_path / "outputs" / "wt_boundary_smoke.json"),
        model_policy={
            "preferred": "glm-5.2",
            "execution_role": "validator",
            "model_family": "glm",
        },
    )


def test_sanitize_redacts_secret_assignments_in_stdout():
    raw = '{"result": "ok", "note": "api_key=supersecret123"}'
    cleaned = sanitize_worker_output(raw)
    assert "supersecret123" not in cleaned
    assert "[redacted]" in cleaned


def test_sanitize_redacts_bearer_tokens_in_stderr():
    token = "abc123" + "def456"
    raw = "Authorization: " + "Bea" + "rer " + token
    cleaned = sanitize_worker_output(raw)
    assert token not in cleaned
    assert "bearer [redacted]" in cleaned.lower()


def test_phase26_current_policy_checks_all_boundaries():
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    check_names = {c.name for c in policy.checks}
    assert "ai_agent_task_packet" in check_names
    assert "model_provider_recorded" in check_names
    assert "timeout_fail_closed" in check_names
    assert "provider_error_fail_closed" in check_names
    assert "output_sanitized" in check_names
    assert "quota_gate_checked" in check_names
    assert "no_subprocess_cli" in check_names
    assert "hermes_auth_boundary" in check_names


def test_phase26_policy_allows_safe_hermes_boundary():
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    result = policy.evaluate(
        ai_agent_task_packet=True,
        model_provider_recorded=True,
        timeout_fail_closed=True,
        provider_error_fail_closed=True,
        output_sanitized=True,
        quota_gate_checked=True,
        no_subprocess_cli=True,
        hermes_auth_boundary=True,
    )
    assert result.status == BoundarySmokeStatus.PASS
    assert result.failed_checks == ()


def test_phase26_policy_fails_closed_on_any_violation():
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    result = policy.evaluate(
        ai_agent_task_packet=False,
        model_provider_recorded=True,
        timeout_fail_closed=True,
        provider_error_fail_closed=True,
        output_sanitized=True,
        quota_gate_checked=True,
        no_subprocess_cli=True,
        hermes_auth_boundary=True,
    )
    assert result.status == BoundarySmokeStatus.FAIL
    assert result.failed_checks[0].name == "ai_agent_task_packet"


def test_phase26_report_records_hermes_provider_boundary():
    report = LiveWorkerBoundarySmokePolicy.current_verified().verification_report()
    assert report["phase"] == "Phase 26"
    assert report["default_runner"] == "hermes_provider_worker"
    assert report["live_cli_execution_in_tests"] == "not_used"
    assert report["output_sanitization"] == "required"


def test_phase26_hermes_provider_runner_sanitizes_output(tmp_path: Path):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="succeeded",
            content="api_key=leaked_secret_123",
            provider=provider,
            model=model,
            completed=True,
        )

    task = _task(tmp_path)
    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    completed = runner.collect(runner.dispatch(task))

    assert completed.state == WorkerTaskState.SUCCEEDED
    output = json.loads(Path(completed.output_path).read_text(encoding="utf-8"))
    assert output["runner"] == "hermes_provider"
    assert "leaked_secret_123" not in output["content"]
    assert "[redacted]" in output["content"]


def test_phase26_hermes_provider_runner_times_out_fail_closed(tmp_path: Path):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="timed_out",
            provider=provider,
            model=model,
            error="timeout",
            timed_out=True,
        )

    task = _task(tmp_path)
    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    completed = runner.collect(runner.dispatch(task))

    assert completed.state == WorkerTaskState.TIMED_OUT
    output = json.loads(Path(completed.output_path).read_text(encoding="utf-8"))
    assert output["timed_out"] is True
