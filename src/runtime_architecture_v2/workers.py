"""Worker execution boundaries for Runtime Architecture v2.

This module keeps live execution behind explicit runner boundaries. Unit tests
use FakeWorkerRunner; opencode-go integration is added as dry-run command and
packet construction before any live subprocess execution.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .schemas import WorkerTask, WorkerTaskState


@dataclass(frozen=True)
class OpenCodeGoRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timeout_occurred: bool = False
    duration_seconds: float = 0.0


OpenCodeGoCommandRunner = Callable[[list[str], int, str | None], OpenCodeGoRunResult]


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


def _prompt_for_task(task: WorkerTask) -> str:
    return (
        f"Execute Runtime Architecture v2 worker task {task.worker_task_id} "
        f"as role {task.role}. Return structured JSON."
    )


def _default_opencode_go_runner(
    command: list[str],
    timeout_seconds: int,
    workdir: str | None,
) -> OpenCodeGoRunResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=workdir,
        )
        return OpenCodeGoRunResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timeout_occurred=False,
            duration_seconds=round(time.monotonic() - start, 4),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return OpenCodeGoRunResult(
            exit_code=-1,
            stdout=stdout,
            stderr=stderr,
            timeout_occurred=True,
            duration_seconds=round(time.monotonic() - start, 4),
        )
    except OSError as exc:
        return OpenCodeGoRunResult(
            exit_code=-1,
            stdout="",
            stderr=f"OSError: {exc}",
            timeout_occurred=False,
            duration_seconds=round(time.monotonic() - start, 4),
        )


class OpenCodeGoWorkerRunner:
    """WorkerRunner implementation for opencode-go packet execution.

    The command runner is injectable; unit tests use a fake callable and never
    execute a live CLI. The default runner is the actual subprocess boundary.
    """

    def __init__(
        self,
        *,
        wrapper: OpenCodeGoPacketWrapper | None = None,
        command_runner: OpenCodeGoCommandRunner | None = None,
        timeout_seconds: int = 300,
        output_format: str = "json",
        workdir: str | None = None,
    ) -> None:
        self.wrapper = wrapper or OpenCodeGoPacketWrapper()
        self.command_runner = command_runner or _default_opencode_go_runner
        self.timeout_seconds = timeout_seconds
        self.output_format = output_format
        self.workdir = workdir

    def dispatch(self, task: WorkerTask) -> WorkerTask:
        self.wrapper.write_packet(
            task,
            prompt=_prompt_for_task(task),
            context={"worker_task": task.to_dict()},
        )
        return replace(task, state=WorkerTaskState.RUNNING)

    def collect(self, task: WorkerTask) -> WorkerTask:
        if task.state != WorkerTaskState.RUNNING:
            raise WorkerRunError(
                code="task_not_running",
                message="worker task must be running before collect",
                worker_task_id=task.worker_task_id,
            )
        packet_path = Path(task.packet_path)
        command = self.wrapper.build_command(
            task,
            packet_path=packet_path,
            timeout_seconds=self.timeout_seconds,
            output_format=self.output_format,
        )
        try:
            result = self.command_runner(command, self.timeout_seconds, self.workdir)
        except Exception:
            output_path = Path(task.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "meeting_run_id": task.meeting_run_id,
                        "worker_task_id": task.worker_task_id,
                        "status": "failed",
                        "command": command,
                        "error": "opencode_go_runner_exception",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return replace(
                task,
                state=WorkerTaskState.FAILED,
                error="opencode_go_runner_exception",
                output_path=str(output_path),
            )
        status, state, error = OpenCodeGoSmokeRunner._classify_result(result)
        output_path = Path(task.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "meeting_run_id": task.meeting_run_id,
                    "worker_task_id": task.worker_task_id,
                    "status": status,
                    "command": command,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "timeout_occurred": result.timeout_occurred,
                    "duration_seconds": result.duration_seconds,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return replace(task, state=state, error=error, output_path=str(output_path))


class OpenCodeGoSmokeRunner:
    """Gated live-smoke boundary for opencode-go execution.

    Output files may contain live stdout/stderr and should stay under ignored
    runtime paths, never committed source paths.
    """

    def __init__(
        self,
        *,
        wrapper: OpenCodeGoPacketWrapper | None = None,
        command_runner: OpenCodeGoCommandRunner | None = None,
        timeout_seconds: int = 120,
        output_format: str = "json",
        workdir: str | None = None,
        expected_stdout_contains: str = "",
    ) -> None:
        self.wrapper = wrapper or OpenCodeGoPacketWrapper()
        self.command_runner = command_runner or _default_opencode_go_runner
        self.timeout_seconds = timeout_seconds
        self.output_format = output_format
        self.workdir = workdir
        self.expected_stdout_contains = expected_stdout_contains

    def run(
        self,
        task: WorkerTask,
        *,
        prompt: str,
        context: dict[str, object],
    ) -> WorkerTask:
        packet_path = self.wrapper.write_packet(task, prompt=prompt, context=context)
        command = self.wrapper.build_command(
            task,
            packet_path=packet_path,
            timeout_seconds=self.timeout_seconds,
            output_format=self.output_format,
        )
        result = self.command_runner(command, self.timeout_seconds, self.workdir)
        status, state, error = self._classify_result(result)
        if (
            state == WorkerTaskState.SUCCEEDED
            and self.expected_stdout_contains
            and self.expected_stdout_contains not in result.stdout
        ):
            status = "failed"
            state = WorkerTaskState.FAILED
            error = "opencode_go_missing_expected_output"
        output_path = Path(task.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "meeting_run_id": task.meeting_run_id,
                    "worker_task_id": task.worker_task_id,
                    "status": status,
                    "command": command,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "timeout_occurred": result.timeout_occurred,
                    "duration_seconds": result.duration_seconds,
                    "expected_stdout_contains": self.expected_stdout_contains,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return replace(task, state=state, error=error, output_path=str(output_path))

    @staticmethod
    def _classify_result(
        result: OpenCodeGoRunResult,
    ) -> tuple[str, WorkerTaskState, str]:
        if result.timeout_occurred:
            return ("timed_out", WorkerTaskState.TIMED_OUT, "opencode_go_timeout")
        if result.exit_code == 0:
            return ("succeeded", WorkerTaskState.SUCCEEDED, "")
        return (
            "failed",
            WorkerTaskState.FAILED,
            f"opencode_go_exit_{result.exit_code}",
        )
