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
    HermesProviderRunResult,
    HermesProviderWorkerRunner,
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


def test_hermes_provider_worker_runner_dispatches_packet_and_collects_via_injected_provider(
    tmp_path: Path,
):
    calls = []

    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        calls.append(
            {
                "provider": provider,
                "model": model,
                "prompt": prompt,
                "timeout_seconds": timeout_seconds,
            }
        )
        return HermesProviderRunResult(
            status="succeeded",
            content='{"status":"ok"}',
            provider=provider,
            model=model,
            duration_seconds=0.5,
            api_calls=1,
            completed=True,
        )

    runner = HermesProviderWorkerRunner(
        completion_runner=completion_runner,
        timeout_seconds=33,
    )
    dispatched = runner.dispatch(_task(tmp_path, "wt_hermes"))
    collected = runner.collect(dispatched)

    assert dispatched.state == WorkerTaskState.RUNNING
    assert collected.state == WorkerTaskState.SUCCEEDED
    assert collected.error == ""
    assert len(calls) == 1
    assert calls[0]["provider"] == "opencode-go"
    assert calls[0]["model"] == "fake"
    assert calls[0]["timeout_seconds"] == 33
    packet = json.loads(Path(dispatched.packet_path).read_text(encoding="utf-8"))
    assert packet["worker_task"]["worker_task_id"] == "wt_hermes"
    assert packet["runner"] == "hermes_provider"
    output = json.loads(Path(collected.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "succeeded"
    assert output["runner"] == "hermes_provider"
    assert output["provider"] == "opencode-go"
    assert output["api_calls"] == 1


def test_hermes_provider_worker_runner_returns_structured_timeout_without_crashing(
    tmp_path: Path,
):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="timed_out",
            provider=provider,
            model=model,
            error="provider timeout",
            timed_out=True,
        )

    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    timed_out = runner.collect(runner.dispatch(_task(tmp_path, "wt_timeout_live")))

    assert timed_out.state == WorkerTaskState.TIMED_OUT
    assert timed_out.error == "provider timeout"
    output = json.loads(Path(timed_out.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "timed_out"
    assert output["timed_out"] is True


def test_hermes_provider_worker_runner_fails_closed_and_sanitizes_error(
    tmp_path: Path,
):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="failed",
            provider=provider,
            model=model,
            error="api_key=secret_token_123",
            completed=False,
        )

    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    failed = runner.collect(runner.dispatch(_task(tmp_path, "wt_provider_fail")))

    assert failed.state == WorkerTaskState.FAILED
    assert "secret_token_123" not in failed.error
    assert "[redacted]" in failed.error
    output = json.loads(Path(failed.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "failed"
    assert "secret_token_123" not in json.dumps(output)
    assert "[redacted]" in output["error"]
