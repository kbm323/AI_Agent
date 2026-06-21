from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import (
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from src.runtime_architecture_v2.workers import FakeWorkerRunner, WorkerRunError


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
    else:  # pragma: no cover
        raise AssertionError("collect should reject non-running task")
