from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import (
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from src.runtime_architecture_v2.workers import (
    OpenCodeGoRunResult,
    OpenCodeGoSmokeRunner,
)


def _task(tmp_path: Path) -> WorkerTask:
    return WorkerTask(
        worker_task_id="wt_live_smoke",
        meeting_run_id="mr_live_smoke",
        role="glm_validator",
        runner=WorkerTaskRunner.OPENCODE_GO,
        packet_path=str(tmp_path / "packets" / "wt_live_smoke.json"),
        output_path=str(tmp_path / "outputs" / "wt_live_smoke.json"),
        model_policy={"preferred": "glm-5.1", "execution_role": "validator"},
    )


def test_smoke_runner_invokes_opencode_go_with_injected_runner_and_writes_output(
    tmp_path: Path,
):
    calls: list[dict[str, object]] = []

    def runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(
            {"command": command, "timeout_seconds": timeout_seconds, "workdir": workdir}
        )
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"status":"ok","message":"OPENCODE_GO_SMOKE_OK"}',
            stderr="",
            timeout_occurred=False,
        )

    task = _task(tmp_path)
    completed = OpenCodeGoSmokeRunner(
        command_runner=runner,
        timeout_seconds=45,
        workdir=str(tmp_path),
    ).run(task, prompt="Return OPENCODE_GO_SMOKE_OK", context={"smoke": True})

    assert completed.state == WorkerTaskState.SUCCEEDED
    assert completed.error == ""
    assert len(calls) == 1
    command = calls[0]["command"]
    assert command[:3] == ["opencode-go", "--model", "glm-5.1"]
    assert "--context-file" in command
    assert "--prompt" in command
    assert calls[0]["timeout_seconds"] == 45
    output = json.loads(Path(completed.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "succeeded"
    assert output["exit_code"] == 0
    assert output["stdout"] == '{"status":"ok","message":"OPENCODE_GO_SMOKE_OK"}'
    assert output["timeout_occurred"] is False
    assert "opencode-go" in output["command"][0]


def test_smoke_runner_reports_nonzero_exit_as_structured_failure(tmp_path: Path):
    def runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=2,
            stdout="",
            stderr="context file not found",
            timeout_occurred=False,
        )

    failed = OpenCodeGoSmokeRunner(command_runner=runner).run(
        _task(tmp_path), prompt="smoke", context={}
    )

    assert failed.state == WorkerTaskState.FAILED
    assert failed.error == "opencode_go_exit_2"
    output = json.loads(Path(failed.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "failed"
    assert output["stderr"] == "context file not found"


def test_smoke_runner_can_require_expected_stdout_token(tmp_path: Path):
    def runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"status":"ok","message":"different"}',
            stderr="",
            timeout_occurred=False,
        )

    failed = OpenCodeGoSmokeRunner(
        command_runner=runner,
        expected_stdout_contains="OPENCODE_GO_SMOKE_OK",
    ).run(_task(tmp_path), prompt="smoke", context={})

    assert failed.state == WorkerTaskState.FAILED
    assert failed.error == "opencode_go_missing_expected_output"
    output = json.loads(Path(failed.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "failed"
    assert output["expected_stdout_contains"] == "OPENCODE_GO_SMOKE_OK"


def test_smoke_runner_reports_timeout_as_structured_timeout(tmp_path: Path):
    def runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=-1,
            stdout="partial",
            stderr="timeout",
            timeout_occurred=True,
        )

    timed_out = OpenCodeGoSmokeRunner(command_runner=runner).run(
        _task(tmp_path), prompt="smoke", context={}
    )

    assert timed_out.state == WorkerTaskState.TIMED_OUT
    assert timed_out.error == "opencode_go_timeout"
    output = json.loads(Path(timed_out.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "timed_out"
    assert output["timeout_occurred"] is True
