from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import (
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from src.runtime_architecture_v2.workers import (
    FakeWorkerRunner,
    OpenCodeGoRunResult,
    OpenCodeGoWorkerRunner,
    WorkerRunError,
)


def _task(tmp_path: Path, worker_task_id: str = "wt_001") -> WorkerTask:
    return WorkerTask(
        worker_task_id=worker_task_id,
        meeting_run_id="mr_001",
        role="software_engineer",
        runner=WorkerTaskRunner.HERMES_WRAPPER,
        packet_path=str(tmp_path / "packet.json"),
        output_path=str(tmp_path / "output.json"),
        model_policy={"preferred": "fake"},
    )


def test_fake_worker_dispatch_writes_packet_and_marks_task_running(tmp_path: Path):
    task = _task(tmp_path)

    dispatched = FakeWorkerRunner().dispatch(task)

    assert dispatched.state == WorkerTaskState.RUNNING
    assert Path(dispatched.packet_path).exists()
    packet = json.loads(Path(dispatched.packet_path).read_text(encoding="utf-8"))
    assert packet["worker_task_id"] == "wt_001"
    assert packet["meeting_run_id"] == "mr_001"
    assert packet["role"] == "software_engineer"
    assert "openclaw" not in json.dumps(packet).lower()


def test_fake_worker_collect_writes_output_and_marks_task_succeeded(tmp_path: Path):
    runner = FakeWorkerRunner(output={"answer": "done"})
    running = runner.dispatch(_task(tmp_path))

    completed = runner.collect(running)

    assert completed.state == WorkerTaskState.SUCCEEDED
    assert completed.error == ""
    output = json.loads(Path(completed.output_path).read_text(encoding="utf-8"))
    assert output["meeting_run_id"] == "mr_001"
    assert output["worker_task_id"] == "wt_001"
    assert output["status"] == "succeeded"
    assert output["result"] == {"answer": "done"}


def test_fake_worker_can_simulate_failure_and_timeout(tmp_path: Path):
    failed = FakeWorkerRunner(fail_with="boom").collect(
        FakeWorkerRunner().dispatch(_task(tmp_path, "wt_fail"))
    )
    timed_out = FakeWorkerRunner(timeout=True).collect(
        FakeWorkerRunner().dispatch(_task(tmp_path, "wt_timeout"))
    )

    assert failed.state == WorkerTaskState.FAILED
    assert failed.error == "boom"
    assert timed_out.state == WorkerTaskState.TIMED_OUT
    assert timed_out.error == "timeout"


def test_collect_requires_dispatched_task(tmp_path: Path):
    task = _task(tmp_path)

    try:
        FakeWorkerRunner().collect(task)
    except WorkerRunError as exc:
        assert exc.code == "task_not_running"
        assert exc.worker_task_id == "wt_001"
        assert str(exc) == (
            "task_not_running: worker task must be running before collect"
        )
    else:  # pragma: no cover
        raise AssertionError("collect should reject non-running task")


def test_opencode_go_worker_runner_dispatches_packet_and_collects_via_injected_cli(
    tmp_path: Path,
):
    calls = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(
            {"command": command, "timeout_seconds": timeout_seconds, "workdir": workdir}
        )
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"status":"ok"}',
            stderr="",
            timeout_occurred=False,
        )

    runner = OpenCodeGoWorkerRunner(
        command_runner=command_runner,
        timeout_seconds=33,
        workdir=str(tmp_path),
    )
    dispatched = runner.dispatch(_task(tmp_path, "wt_opencode"))
    collected = runner.collect(dispatched)

    assert dispatched.state == WorkerTaskState.RUNNING
    assert collected.state == WorkerTaskState.SUCCEEDED
    assert collected.error == ""
    assert len(calls) == 1
    command = calls[0]["command"]
    assert command[:3] == ["opencode-go", "--model", "fake"]
    assert "--context-file" in command
    assert calls[0]["timeout_seconds"] == 33
    packet = json.loads(Path(dispatched.packet_path).read_text(encoding="utf-8"))
    assert packet["worker_task"]["worker_task_id"] == "wt_opencode"
    assert packet["runner"] == "opencode_go"
    output = json.loads(Path(collected.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "succeeded"
    assert output["exit_code"] == 0


def test_opencode_go_worker_runner_returns_structured_timeout_without_crashing(
    tmp_path: Path,
):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=-1,
            stdout="partial",
            stderr="timeout",
            timeout_occurred=True,
        )

    runner = OpenCodeGoWorkerRunner(command_runner=command_runner)
    timed_out = runner.collect(runner.dispatch(_task(tmp_path, "wt_timeout_live")))

    assert timed_out.state == WorkerTaskState.TIMED_OUT
    assert timed_out.error == "opencode_go_timeout"
    output = json.loads(Path(timed_out.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "timed_out"
    assert output["timeout_occurred"] is True


def test_opencode_go_worker_runner_fails_closed_when_injected_runner_raises(
    tmp_path: Path,
):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        raise RuntimeError("runner exploded with secret token")

    runner = OpenCodeGoWorkerRunner(command_runner=command_runner)
    failed = runner.collect(runner.dispatch(_task(tmp_path, "wt_runner_exception")))

    assert failed.state == WorkerTaskState.FAILED
    assert failed.error == "opencode_go_runner_exception"
    output = json.loads(Path(failed.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "failed"
    assert output["error"] == "opencode_go_runner_exception"
    assert "secret token" not in json.dumps(output)
