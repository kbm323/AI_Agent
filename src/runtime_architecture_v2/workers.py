"""Worker execution boundaries for Runtime Architecture v2.

The live worker boundary is Hermes-first: AI_Agent owns WorkerTask state and
payload/evidence files, while provider/auth/model/fallback resolution is
delegated to Hermes' provider runtime.  Legacy opencode-go CLI execution is
not part of the default production path.
"""

from __future__ import annotations

import json
import importlib
import sys
import time
from collections.abc import Callable
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
        _require_running(task)
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


@dataclass(frozen=True)
class HermesProviderRunResult:
    """Normalized result from Hermes provider execution."""

    status: str
    content: str = ""
    provider: str = "opencode-go"
    model: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    api_calls: int = 0
    completed: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class OpenCodeGoRunResult:
    """Legacy injected-runner result shape used only by compatibility tests."""

    exit_code: int
    stdout: str
    stderr: str
    timeout_occurred: bool = False
    duration_seconds: float = 0.0

HermesProviderCompletionRunner = Callable[
    [str, str, str, int], HermesProviderRunResult
]
OpenCodeGoCommandRunner = Callable[..., object]


def _prompt_for_task(task: WorkerTask) -> str:
    return (
        f"Execute Runtime Architecture v2 worker task {task.worker_task_id} "
        f"as role {task.role}. Return structured JSON."
    )


def _worker_context(task: WorkerTask) -> dict[str, object]:
    return {
        "worker_task": task.to_dict(),
        "runner": "hermes_provider",
        "provider": "opencode-go",
    }


def _write_worker_packet(task: WorkerTask, *, prompt: str) -> Path:
    packet_path = Path(task.packet_path)
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker_task": task.to_dict(),
        "prompt": prompt,
        "context": _worker_context(task),
        "runner": "hermes_provider",
    }
    packet_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return packet_path


def _default_hermes_provider_completion(
    provider: str,
    model: str,
    prompt: str,
    timeout_seconds: int,
) -> HermesProviderRunResult:
    """Call Hermes' provider runtime through a minimal no-tools AIAgent.

    This intentionally uses Hermes' provider/auth/model surface instead of
    constructing HTTP clients or loading provider credentials in AI_Agent.
    """

    started = time.monotonic()
    hermes_root = Path.home() / ".hermes" / "hermes-agent"
    if hermes_root.is_dir() and str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))

    try:
        resolve_runtime_provider = importlib.import_module(
            "hermes_cli.runtime_provider"
        ).resolve_runtime_provider
        AIAgent = importlib.import_module("run_agent").AIAgent

        runtime = resolve_runtime_provider(
            requested=provider,
            target_model=model,
        )
        agent = AIAgent(
            provider=runtime["provider"],
            api_mode=runtime["api_mode"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
            model=model,
            enabled_toolsets=[],
            disabled_toolsets=["*"],
            max_iterations=1,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        result = agent.run_conversation(
            prompt,
            system_message=(
                "You are a single AI_Agent worker role. Do not call tools. "
                "Return only the role output requested by the prompt."
            ),
            conversation_history=[],
        )
        content = str(result.get("final_response") or "").strip()
        completed = bool(result.get("completed")) and bool(content)
        return HermesProviderRunResult(
            status="succeeded" if completed else "failed",
            content=content,
            provider=str(result.get("provider") or runtime["provider"]),
            model=str(result.get("model") or model),
            error="" if completed else "hermes_provider_empty_response",
            duration_seconds=round(time.monotonic() - started, 4),
            api_calls=int(result.get("api_calls") or 0),
            completed=completed,
        )
    except TimeoutError as exc:
        return HermesProviderRunResult(
            status="timed_out",
            provider=provider,
            model=model,
            error=f"hermes_provider_timeout: {exc}",
            duration_seconds=round(time.monotonic() - started, 4),
            timed_out=True,
        )
    except Exception as exc:
        return HermesProviderRunResult(
            status="failed",
            provider=provider,
            model=model,
            error=f"hermes_provider_error: {exc}",
            duration_seconds=round(time.monotonic() - started, 4),
        )


class HermesProviderWorkerRunner:
    """WorkerRunner backed by Hermes provider/auth/model runtime."""

    def __init__(
        self,
        *,
        provider: str = "opencode-go",
        model: str = "glm-5.2",
        completion_runner: HermesProviderCompletionRunner | None = None,
        command_runner: OpenCodeGoCommandRunner | None = None,
        timeout_seconds: int = 120,
        workdir: str | None = None,
        **_legacy_kwargs: object,
    ) -> None:
        self.provider = provider
        self.model = model
        self.workdir = workdir
        self.completion_runner = (
            completion_runner
            or _legacy_command_runner_adapter(command_runner)
            or _default_hermes_provider_completion
        )
        self.timeout_seconds = timeout_seconds

    def dispatch(self, task: WorkerTask) -> WorkerTask:
        _write_worker_packet(task, prompt=_prompt_for_task(task))
        return replace(task, state=WorkerTaskState.RUNNING)

    def collect(self, task: WorkerTask) -> WorkerTask:
        _require_running(task)
        prompt = _prompt_for_task(task)
        model = str(task.model_policy.get("preferred") or self.model)
        result = self.completion_runner(
            self.provider,
            model,
            prompt,
            self.timeout_seconds,
        )
        output_path = Path(task.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        state = _state_for_provider_result(result)
        from .worker_boundary_smoke import sanitize_worker_output
        sanitized_error = sanitize_worker_output(result.error)
        error = sanitized_error if state != WorkerTaskState.SUCCEEDED else ""

        output_payload = {
            "meeting_run_id": task.meeting_run_id,
            "worker_task_id": task.worker_task_id,
            "role": task.role,
            "status": result.status,
            "runner": "hermes_provider",
            "provider": result.provider,
            "model": result.model or model,
            "content": sanitize_worker_output(result.content),
            "error": sanitized_error,
            "duration_seconds": result.duration_seconds,
            "api_calls": result.api_calls,
            "completed": result.completed,
            "timed_out": result.timed_out,
        }
        output_path.write_text(
            json.dumps(output_payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return replace(task, state=state, error=error, output_path=str(output_path))


# Compatibility name during the cleanup phase.  The implementation is Hermes-first;
# it no longer executes opencode-go via subprocess.
OpenCodeGoWorkerRunner = HermesProviderWorkerRunner


def _legacy_command_runner_adapter(
    command_runner: OpenCodeGoCommandRunner | None,
) -> HermesProviderCompletionRunner | None:
    """Adapt old injected tests while keeping the default path Hermes-first."""

    if command_runner is None:
        return None

    def run(provider: str, model: str, prompt: str, timeout_seconds: int) -> HermesProviderRunResult:
        del prompt
        started = time.monotonic()
        try:
            legacy = command_runner(
                ["hermes-provider", "--provider", provider, "--model", model],
                timeout_seconds=timeout_seconds,
                workdir=None,
            )
            stdout = str(getattr(legacy, "stdout", "") or "")
            stderr = str(getattr(legacy, "stderr", "") or "")
            exit_code = int(getattr(legacy, "exit_code", 0))
            timed_out = bool(getattr(legacy, "timeout_occurred", False))
            completed = exit_code == 0 and not timed_out and bool(stdout.strip())
            return HermesProviderRunResult(
                status="succeeded" if completed else ("timed_out" if timed_out else "failed"),
                content=stdout.strip(),
                provider=provider,
                model=model,
                error=stderr.strip(),
                duration_seconds=round(time.monotonic() - started, 4),
                completed=completed,
                timed_out=timed_out,
            )
        except Exception as exc:
            del exc
            return HermesProviderRunResult(
                status="failed",
                provider=provider,
                model=model,
                error="legacy_runner_adapter_error",
                duration_seconds=round(time.monotonic() - started, 4),
            )

    return run


def _require_running(task: WorkerTask) -> None:
    if task.state != WorkerTaskState.RUNNING:
        raise WorkerRunError(
            code="task_not_running",
            message="worker task must be running before collect",
            worker_task_id=task.worker_task_id,
        )


def _state_for_provider_result(result: HermesProviderRunResult) -> WorkerTaskState:
    if result.timed_out or result.status == "timed_out":
        return WorkerTaskState.TIMED_OUT
    if result.status == "succeeded" and result.completed and result.content.strip():
        return WorkerTaskState.SUCCEEDED
    return WorkerTaskState.FAILED


__all__ = [
    "FakeWorkerRunner",
    "HermesProviderCompletionRunner",
    "HermesProviderRunResult",
    "HermesProviderWorkerRunner",
    "OpenCodeGoCommandRunner",
    "OpenCodeGoRunResult",
    "OpenCodeGoWorkerRunner",
    "WorkerRunError",
    "WorkerRunner",
]
