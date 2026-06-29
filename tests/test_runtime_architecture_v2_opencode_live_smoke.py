"""Hermes provider live-smoke boundary tests.

These tests intentionally avoid opencode-go CLI/subprocess. They exercise the
same fail-closed and sanitization semantics through injected Hermes provider
results.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import (
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from src.runtime_architecture_v2.workers import (
    HermesProviderRunResult,
    HermesProviderWorkerRunner,
)


def _task(tmp_path: Path) -> WorkerTask:
    return WorkerTask(
        worker_task_id="wt_live_smoke",
        meeting_run_id="mr_live_smoke",
        role="content_lead",
        runner=WorkerTaskRunner.HERMES_WRAPPER,
        packet_path=str(tmp_path / "packets" / "wt_live_smoke.json"),
        output_path=str(tmp_path / "outputs" / "wt_live_smoke.json"),
        model_policy={"preferred": "glm-5.2"},
    )


def test_hermes_provider_smoke_succeeds_with_expected_output(tmp_path: Path):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="succeeded",
            content='{"status":"ok"}',
            provider=provider,
            model=model,
            completed=True,
            api_calls=1,
        )

    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    completed = runner.collect(runner.dispatch(_task(tmp_path)))

    assert completed.state == WorkerTaskState.SUCCEEDED
    output = json.loads(Path(completed.output_path).read_text(encoding="utf-8"))
    assert output["runner"] == "hermes_provider"
    assert output["provider"] == "opencode-go"
    assert output["model"] == "glm-5.2"
    assert output["content"] == '{"status":"ok"}'


def test_hermes_provider_smoke_fails_closed_on_provider_error(tmp_path: Path):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="failed",
            provider=provider,
            model=model,
            error="provider unavailable",
            completed=False,
        )

    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    failed = runner.collect(runner.dispatch(_task(tmp_path)))

    assert failed.state == WorkerTaskState.FAILED
    output = json.loads(Path(failed.output_path).read_text(encoding="utf-8"))
    assert output["status"] == "failed"
    assert output["error"] == "provider unavailable"


def test_hermes_provider_smoke_times_out_fail_closed(tmp_path: Path):
    def completion_runner(provider: str, model: str, prompt: str, timeout_seconds: int):
        return HermesProviderRunResult(
            status="timed_out",
            provider=provider,
            model=model,
            error="timeout",
            timed_out=True,
        )

    runner = HermesProviderWorkerRunner(completion_runner=completion_runner)
    timed_out = runner.collect(runner.dispatch(_task(tmp_path)))

    assert timed_out.state == WorkerTaskState.TIMED_OUT
    output = json.loads(Path(timed_out.output_path).read_text(encoding="utf-8"))
    assert output["timed_out"] is True
