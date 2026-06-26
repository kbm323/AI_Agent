"""Phase 26 live worker/validator/auditor boundary smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from runtime_architecture_v2.schemas import (
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from runtime_architecture_v2.worker_boundary_smoke import (
    BoundarySmokeStatus,
    LiveWorkerBoundarySmokePolicy,
    sanitize_worker_output,
)
from runtime_architecture_v2.workers import (
    OpenCodeGoRunResult,
    OpenCodeGoSmokeRunner,
    OpenCodeGoWorkerRunner,
    _explicit_env_for_subprocess,
    _resolve_executable_for_explicit_env,
)


def _task(tmp_path: Path) -> WorkerTask:
    return WorkerTask(
        worker_task_id="wt_boundary_smoke",
        meeting_run_id="mr_boundary_smoke",
        role="glm_validator",
        runner=WorkerTaskRunner.OPENCODE_GO,
        packet_path=str(tmp_path / "packets" / "wt_boundary_smoke.json"),
        output_path=str(tmp_path / "outputs" / "wt_boundary_smoke.json"),
        model_policy={
            "preferred": "glm-5.1",
            "execution_role": "validator",
            "model_family": "glm",
        },
    )


# ── Output sanitization ─────────────────────────────────────────────


def test_sanitize_redacts_secret_assignments_in_stdout():
    raw = '{"result": "ok", "note": "api_key=supersecret123"}'
    cleaned = sanitize_worker_output(raw)
    assert "supersecret123" not in cleaned
    assert "[redacted]" in cleaned


def test_sanitize_redacts_bearer_tokens_in_stderr():
    raw = "Authorization: Bearer abc123def456"
    cleaned = sanitize_worker_output(raw)
    assert "abc123def456" not in cleaned
    assert "bearer [redacted]" in cleaned.lower()


def test_sanitize_preserves_normal_output():
    raw = '{"status": "ok", "answer": "normal content"}'
    cleaned = sanitize_worker_output(raw)
    assert cleaned == raw


def test_sanitize_truncates_overlong_output():
    raw = "x" * 6000
    cleaned = sanitize_worker_output(raw)
    assert len(cleaned) <= 4096


# ── Boundary smoke policy ───────────────────────────────────────────


def test_phase26_current_policy_checks_all_boundaries():
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    checks = policy.checks
    check_names = {c.name for c in checks}
    assert "packet_based_input" in check_names
    assert "model_provider_recorded" in check_names
    assert "timeout_fail_closed" in check_names
    assert "nonzero_exit_fail_closed" in check_names
    assert "output_sanitized" in check_names
    assert "quota_gate_checked" in check_names
    assert "no_shell_true" in check_names
    assert "no_direct_env_passthrough" in check_names


def test_phase26_policy_allows_safe_boundary(tmp_path: Path):
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    result = policy.evaluate(
        uses_packet_input=True,
        model_provider_recorded=True,
        timeout_fail_closed=True,
        nonzero_exit_fail_closed=True,
        output_sanitized=True,
        quota_gate_checked=True,
        no_shell_true=True,
        no_direct_env_passthrough=True,
    )
    assert result.status == BoundarySmokeStatus.PASS
    assert result.failed_checks == ()


def test_phase26_policy_fails_closed_on_any_violation(tmp_path: Path):
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    result = policy.evaluate(
        uses_packet_input=False,
        model_provider_recorded=True,
        timeout_fail_closed=True,
        nonzero_exit_fail_closed=True,
        output_sanitized=True,
        quota_gate_checked=True,
        no_shell_true=True,
        no_direct_env_passthrough=True,
    )
    assert result.status == BoundarySmokeStatus.FAIL
    assert len(result.failed_checks) == 1
    assert result.failed_checks[0].name == "packet_based_input"


def test_phase26_policy_fails_on_unsanitized_output():
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    result = policy.evaluate(
        uses_packet_input=True,
        model_provider_recorded=True,
        timeout_fail_closed=True,
        nonzero_exit_fail_closed=True,
        output_sanitized=False,
        quota_gate_checked=True,
        no_shell_true=True,
        no_direct_env_passthrough=True,
    )
    assert result.status == BoundarySmokeStatus.FAIL
    assert result.failed_checks[0].name == "output_sanitized"


def test_phase26_policy_fails_on_shell_true():
    policy = LiveWorkerBoundarySmokePolicy.current_verified()
    result = policy.evaluate(
        uses_packet_input=True,
        model_provider_recorded=True,
        timeout_fail_closed=True,
        nonzero_exit_fail_closed=True,
        output_sanitized=True,
        quota_gate_checked=True,
        no_shell_true=False,
        no_direct_env_passthrough=True,
    )
    assert result.status == BoundarySmokeStatus.FAIL
    assert result.failed_checks[0].name == "no_shell_true"


def test_phase26_report_records_gate6_status():
    report = LiveWorkerBoundarySmokePolicy.current_verified(
    ).verification_report()
    assert report["phase"] == "Phase 26"
    assert report["gate_6_worker_validator_auditor_boundary"] == (
        "VERIFIED_BOUNDARY_SMOKE_POLICY_EXISTS"
    )
    assert report["gate_7_quota_cost_monitoring"] == "AVAILABLE"
    assert report["default_runner"] == "opencode_go_packet_injected"
    assert report["live_cli_execution_in_tests"] == "not_allowed"
    assert report["output_sanitization"] == "required"


# ── Integrated smoke with sanitization ──────────────────────────────


def test_phase26_smoke_runner_output_is_sanitized_when_secrets_present(
    tmp_path: Path,
):
    def runner(
        command: list[str], timeout_seconds: int, workdir: str | None
    ):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=(
                '{"status":"ok","note":"api_key=leaked_secret_123"}'
            ),
            stderr="Bearer abc456",
            timeout_occurred=False,
        )

    task = _task(tmp_path)
    completed = OpenCodeGoSmokeRunner(
        command_runner=runner,
        sanitize_output=True,
    ).run(task, prompt="smoke", context={})

    assert completed.state == WorkerTaskState.SUCCEEDED
    output = json.loads(
        Path(completed.output_path).read_text(encoding="utf-8")
    )
    assert "leaked_secret_123" not in output["stdout"]
    assert "abc456" not in output["stderr"]
    assert "[redacted]" in output["stdout"]
    assert "bearer [redacted]" in output["stderr"].lower()


def test_phase26_smoke_runner_preserves_output_when_no_secrets(
    tmp_path: Path,
):
    def runner(
        command: list[str], timeout_seconds: int, workdir: str | None
    ):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"status":"ok","answer":"clean"}',
            stderr="",
            timeout_occurred=False,
        )

    task = _task(tmp_path)
    completed = OpenCodeGoSmokeRunner(
        command_runner=runner,
    ).run(task, prompt="smoke", context={})

    output = json.loads(
        Path(completed.output_path).read_text(encoding="utf-8")
    )
    assert output["stdout"] == '{"status":"ok","answer":"clean"}'


def test_phase26_worker_runner_sanitizes_output_by_default(tmp_path: Path):
    def runner(
        command: list[str], timeout_seconds: int, workdir: str | None
    ):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout="api_key=leaked_secret_123",
            stderr="Bearer abc456",
            timeout_occurred=False,
        )

    task = _task(tmp_path)
    worker = OpenCodeGoWorkerRunner(command_runner=runner)
    completed = worker.collect(worker.dispatch(task))

    output = json.loads(
        Path(completed.output_path).read_text(encoding="utf-8")
    )
    assert completed.state == WorkerTaskState.SUCCEEDED
    assert "leaked_secret_123" not in output["stdout"]
    assert "abc456" not in output["stderr"]
    assert "[redacted]" in output["stdout"]
    assert "bearer [redacted]" in output["stderr"].lower()


def test_phase26_smoke_runner_sanitizes_output_by_default(tmp_path: Path):
    def runner(
        command: list[str], timeout_seconds: int, workdir: str | None
    ):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout="token=leaked_secret_456",
            stderr="Bearer def789",
            timeout_occurred=False,
        )

    task = _task(tmp_path)
    completed = OpenCodeGoSmokeRunner(command_runner=runner).run(
        task, prompt="smoke", context={},
    )

    output = json.loads(
        Path(completed.output_path).read_text(encoding="utf-8")
    )
    assert completed.state == WorkerTaskState.SUCCEEDED
    assert "leaked_secret_456" not in output["stdout"]
    assert "def789" not in output["stderr"]


def test_phase26_smoke_runner_exception_fails_closed_without_leaking(
    tmp_path: Path,
):
    def runner(
        command: list[str], timeout_seconds: int, workdir: str | None
    ):
        raise RuntimeError("token=supersecret raw failure")

    task = _task(tmp_path)
    completed = OpenCodeGoSmokeRunner(command_runner=runner).run(
        task, prompt="smoke", context={},
    )

    output = json.loads(
        Path(completed.output_path).read_text(encoding="utf-8")
    )
    assert completed.state == WorkerTaskState.FAILED
    assert completed.error == "opencode_go_runner_exception"
    assert output["status"] == "failed"
    assert output["error"] == "opencode_go_runner_exception"
    assert "supersecret" not in json.dumps(output)
    assert "raw failure" not in json.dumps(output)


def test_phase26_default_runner_uses_explicit_env_mapping():
    runner = OpenCodeGoWorkerRunner()
    assert runner.command_env == {}


def test_phase26_explicit_empty_env_keeps_pathless_credentials_but_resolves_binary(
    monkeypatch,
):
    monkeypatch.setenv("PATH", "/safe/bin:/home/kbm/.local/bin")

    def fake_which(binary: str):
        assert binary == "opencode-go"
        return "/home/kbm/.local/bin/opencode-go"

    monkeypatch.setattr("runtime_architecture_v2.workers.shutil.which", fake_which)

    command = ["opencode-go", "--model", "qwen-max"]
    resolved = _resolve_executable_for_explicit_env(command)

    assert resolved[0] == "/home/kbm/.local/bin/opencode-go"
    assert resolved[1:] == ["--model", "qwen-max"]


def test_phase26_explicit_empty_env_allows_path_but_no_token_leak(monkeypatch):
    monkeypatch.setenv("PATH", "/safe/bin:/home/kbm/.local/bin")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "must_not_leak")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "must_not_leak")

    env = _explicit_env_for_subprocess({})

    assert env == {"PATH": "/safe/bin:/home/kbm/.local/bin"}
    assert "OPENCODE_GO_API_KEY" not in env
    assert "DISCORD_BOT_TOKEN" not in env


def test_phase26_smoke_runner_uses_explicit_env_mapping():
    runner = OpenCodeGoSmokeRunner()
    assert runner.command_env == {}


def test_phase26_smoke_runner_redacts_secret_like_prompt_in_command_artifact(
    tmp_path: Path,
):
    def runner(
        command: list[str], timeout_seconds: int, workdir: str | None
    ):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"status":"ok"}',
            stderr="",
            timeout_occurred=False,
        )

    task = _task(tmp_path)
    completed = OpenCodeGoSmokeRunner(command_runner=runner).run(
        task,
        prompt="do work with token=prompt_secret_123",
        context={},
    )

    output = json.loads(
        Path(completed.output_path).read_text(encoding="utf-8")
    )
    assert completed.state == WorkerTaskState.SUCCEEDED
    assert "prompt_secret_123" not in json.dumps(output)
    assert "[redacted]" in json.dumps(output)
