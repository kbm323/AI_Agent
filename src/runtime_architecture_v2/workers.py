"""Worker execution boundaries for Runtime Architecture v2.

This module keeps live execution behind explicit runner boundaries. Unit tests
use FakeWorkerRunner; opencode-go integration is added as dry-run command and
packet construction before any live subprocess execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .schemas import WorkerTask, WorkerTaskState


@dataclass(frozen=True)
class WorkerRunError(Exception):
    code: str
    message: str
    worker_task_id: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class WorkerRunner(Protocol):
    def dispatch(self, task: WorkerTask) -> WorkerTask:
        """Write or submit a worker task and return updated task metadata."""
        raise NotImplementedError

    def collect(self, task: WorkerTask) -> WorkerTask:
        """Collect worker output and return updated task metadata."""
        raise NotImplementedError


class FakeWorkerRunner:
    """Deterministic local runner for tests and simulation."""

    def __init__(
        self,
        *,
        output: dict[str, object] | None = None,
        fail_with: str = "",
        timeout: bool = False,
    ) -> None:
        self._output = dict(output or {"answer": "ok"})
        self._fail_with = fail_with
        self._timeout = timeout

    def dispatch(self, task: WorkerTask) -> WorkerTask:
        packet_path = Path(task.packet_path)
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return replace(task, state=WorkerTaskState.RUNNING)

    def collect(self, task: WorkerTask) -> WorkerTask:
        if task.state != WorkerTaskState.RUNNING:
            raise WorkerRunError(
                code="task_not_running",
                message="worker task must be running before collect",
                worker_task_id=task.worker_task_id,
            )

        if self._timeout:
            return replace(task, state=WorkerTaskState.TIMED_OUT, error="timeout")
        if self._fail_with:
            return replace(task, state=WorkerTaskState.FAILED, error=self._fail_with)

        output_path = Path(task.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_payload = {
            "meeting_run_id": task.meeting_run_id,
            "worker_task_id": task.worker_task_id,
            "role": task.role,
            "status": "succeeded",
            "result": self._output,
        }
        output_path.write_text(
            json.dumps(output_payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return replace(
            task,
            state=WorkerTaskState.SUCCEEDED,
            output_path=str(output_path),
        )


class OpenCodeGoPacketWrapper:
    """Build opencode-go packets and commands without executing them."""

    def __init__(self, *, binary: str = "opencode-go") -> None:
        self.binary = binary

    def write_packet(
        self,
        task: WorkerTask,
        *,
        prompt: str,
        context: dict[str, object],
    ) -> Path:
        packet_path = Path(task.packet_path)
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "worker_task": task.to_dict(),
            "prompt": prompt,
            "context": dict(context),
            "dry_run": True,
            "runner": "opencode_go",
        }
        packet_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return packet_path

    def build_command(
        self,
        task: WorkerTask,
        *,
        packet_path: Path,
        timeout_seconds: int = 300,
        output_format: str = "json",
    ) -> list[str]:
        model = str(task.model_policy.get("preferred") or task.role)
        prompt = self._prompt_from_packet(packet_path)
        return [
            self.binary,
            "--model",
            model,
            "--context-file",
            str(packet_path),
            "--timeout-seconds",
            str(timeout_seconds),
            "--prompt",
            prompt,
            "--format",
            output_format,
        ]

    @staticmethod
    def _prompt_from_packet(packet_path: Path) -> str:
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
        return str(payload.get("prompt") or "")
