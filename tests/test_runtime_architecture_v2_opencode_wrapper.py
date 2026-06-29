"""Hermes provider worker packet tests.

The old opencode-go CLI packet wrapper is intentionally gone from the default
runtime path. AI_Agent now persists WorkerTask packets for audit, then delegates
provider/auth/model execution to Hermes.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import WorkerTask, WorkerTaskRunner
from src.runtime_architecture_v2.workers import HermesProviderWorkerRunner


def _task(tmp_path: Path) -> WorkerTask:
    return WorkerTask(
        worker_task_id="wt_packet",
        meeting_run_id="mr_packet",
        role="content_lead",
        runner=WorkerTaskRunner.HERMES_WRAPPER,
        packet_path=str(tmp_path / "packet.json"),
        output_path=str(tmp_path / "output.json"),
        model_policy={"preferred": "glm-5.2"},
    )


def test_hermes_worker_dispatch_writes_audit_packet_not_cli_command(tmp_path: Path):
    task = _task(tmp_path)
    dispatched = HermesProviderWorkerRunner().dispatch(task)

    packet = json.loads(Path(dispatched.packet_path).read_text(encoding="utf-8"))
    assert packet["runner"] == "hermes_provider"
    assert packet["context"]["provider"] == "opencode-go"
    assert packet["worker_task"]["worker_task_id"] == "wt_packet"
    packet_text = json.dumps(packet)
    assert "--context-file" not in packet_text
    assert "opencode-go" in packet_text
