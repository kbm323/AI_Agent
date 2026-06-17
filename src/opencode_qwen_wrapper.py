"""opencode-go CLI invocation wrapper for the Qwen model.

Sub-AC 2c-2: Encapsulates the CLI command construction, execution, and
raw stdout capture for invoking the Qwen model through the opencode-go
CLI.  Designed to be independently testable by injecting a mock
subprocess runner instead of making real CLI calls.

Architecture
------------
Every call constructs a deterministic CLI command, executes it via the
injectable ``_subprocess_runner``, and returns a frozen
``OpencodeCallResult`` with stdout, stderr, exit code, timing, and
success flag.

The module enforces the file-based I/O contract from the system design
(Track 5): ``--prompt`` inline is forbidden; context is passed via
``--context-file``.  The raw stdout is returned untouched — JSON
extraction, schema validation, and classification parsing are handled
by upstream modules (``qwen_json_extractor``, ``qwen_field_validator``,
``qwen_router.parse_classification_response``).

Usage::

    from src.opencode_qwen_wrapper import (
        OpencodeCallConfig,
        OpencodeCallResult,
        invoke_qwen,
    )

    config = OpencodeCallConfig(
        model="qwen-max",
        context_file="/path/to/packet.json",
    )
    result = invoke_qwen(config)
    if result.success:
        raw_output = result.stdout
    else:
        handle_failure(result)
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable


# ── Configuration ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OpencodeCallConfig:
    """Configuration for a single opencode-go CLI invocation.

    Attributes:
        model: Model name to pass via ``--model`` (e.g. ``"qwen-max"``).
        context_file: Path to the JSON context packet file injected via
                      ``--context-file``.  This is the *only* supported
                      prompt input method — inline ``--prompt`` is
                      forbidden per the Track 5 design contract.
        timeout_seconds: Maximum wall-clock time for the subprocess.
                         Default 120s.  Must be >= 1.
        env: Optional extra environment variables passed to the
             subprocess (merged with os.environ).
        workdir: Optional working directory for the subprocess.
    """

    model: str
    """LLM model name for ``--model`` (e.g. ``qwen-max``)."""

    context_file: str
    """Path to context packet JSON file (``--context-file`` argument)."""

    timeout_seconds: float = 120.0
    """Maximum wall-clock time for the subprocess (seconds)."""

    env: dict[str, str] | None = None
    """Extra environment variables merged with os.environ."""

    workdir: str | None = None
    """Working directory for the subprocess."""

    def __post_init__(self) -> None:
        if not self.model or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not self.context_file or not self.context_file.strip():
            raise ValueError("context_file must be a non-empty path")
        if self.timeout_seconds < 1:
            raise ValueError(
                f"timeout_seconds must be >= 1, got {self.timeout_seconds}"
            )


# ── Result ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OpencodeCallResult:
    """Outcome of a single opencode-go CLI invocation.

    Attributes:
        success: ``True`` when the subprocess exited with code 0
                 within the timeout.
        exit_code: The raw exit code from the subprocess
                   (``-1`` when a timeout or internal error occurred).
        stdout: Raw captured stdout from the subprocess.
        stderr: Raw captured stderr from the subprocess.
        duration_seconds: Wall-clock elapsed time for the call.
        model: The model that was invoked (mirrored from config for
               logging / tracing).
        context_file: The context file path used (mirrored from config).
        timeout_occurred: ``True`` when the subprocess was killed due
                          to timeout.
        error_message: Human-readable error description when ``success``
                       is ``False``, empty string otherwise.
    """

    success: bool
    """Did the CLI call succeed (exit 0, no timeout)?"""

    exit_code: int
    """Raw subprocess exit code (-1 on timeout/internal error)."""

    stdout: str
    """Raw captured stdout."""

    stderr: str
    """Raw captured stderr."""

    duration_seconds: float
    """Wall-clock elapsed time."""

    model: str
    """Model name used for this call."""

    context_file: str
    """Context file path used for this call."""

    timeout_occurred: bool = False
    """True when the call was killed due to timeout."""

    error_message: str = ""
    """Human-readable error description on failure."""

    @property
    def has_stderr_output(self) -> bool:
        """``True`` when stderr contains content (may indicate warnings)."""
        return bool(self.stderr and self.stderr.strip())


# ── Subprocess runner type (for dependency injection) ───────────────────

#: Signature of the injectable subprocess runner.
#:
#: Receives (command_list, timeout_seconds, env, workdir) and returns
#: (exit_code, stdout_str, stderr_str).
#:
#: When *exit_code* is ``-1``, the wrapper interprets this as an internal
#: error (timeout, OSError, etc.) rather than a process exit status.
SubprocessRunner = Callable[
    [list[str], float, dict[str, str] | None, str | None],
    tuple[int, str, str],
]


# ── Default subprocess runner (production path) ─────────────────────────


def _default_subprocess_runner(
    command: list[str],
    timeout_seconds: float,
    env: dict[str, str] | None,
    workdir: str | None,
) -> tuple[int, str, str]:
    """Execute a command via ``subprocess.run`` and return (code, stdout, stderr).

    This is the production runner, called when no mock is injected.
    Uses ``subprocess.run`` with ``capture_output=True`` and
    ``text=True`` for string-based stdout/stderr.

    Args:
        command: The full CLI command as a list of strings.
        timeout_seconds: Timeout passed to ``subprocess.run``.
        env: Environment variables merged with ``os.environ``.
        workdir: Working directory (``cwd`` for subprocess).

    Returns:
        ``(exit_code, stdout, stderr)`` — ``exit_code`` is ``-1`` on
        ``TimeoutExpired`` or ``OSError``.
    """
    import os

    merged_env = None
    if env is not None:
        merged_env = {**os.environ, **env}

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=merged_env,
            cwd=workdir,
        )
        return (completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        # Capture whatever partial output exists
        partial_stdout = exc.stdout if isinstance(exc.stdout, str) else (
            exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        )
        partial_stderr = exc.stderr if isinstance(exc.stderr, str) else (
            exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        )
        return (-1, partial_stdout, partial_stderr)
    except OSError as exc:
        return (-1, "", f"OSError: {exc}")


# ── Command builder ──────────────────────────────────────────────────────


def build_opencode_command(model: str, context_file: str) -> list[str]:
    """Construct the ``opencode-go`` CLI command as a list of strings.

    Follows the Track 5 CLI contract:
    ``opencode-go --model <model> --context-file <packet>``

    ``--prompt`` inline is deliberately NOT supported — the system
    design forbids it to avoid ARG_MAX and shell-escaping issues.

    Args:
        model: Model name for ``--model`` (e.g. ``"qwen-max"``).
        context_file: Path to the JSON context packet file for
                      ``--context-file``.

    Returns:
        Command list ready for ``subprocess.run``.  Example::

            ["opencode-go", "--model", "qwen-max",
             "--context-file", "/tmp/packet.json"]

    Raises:
        ValueError: If *model* or *context_file* is empty/whitespace.
    """
    if not model or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not context_file or not context_file.strip():
        raise ValueError("context_file must be a non-empty string")

    return [
        "opencode-go",
        "--model",
        model.strip(),
        "--context-file",
        context_file.strip(),
    ]


# ── Public API ───────────────────────────────────────────────────────────


# Module-level reference to the active subprocess runner.
# Swapped during testing via ``inject_runner()``.
_runner: SubprocessRunner = _default_subprocess_runner


def inject_runner(runner: SubprocessRunner | None) -> None:
    """Replace the active subprocess runner (for testing).

    Pass ``None`` to restore the default production runner.

    Args:
        runner: The test double, or ``None`` to reset to default.
    """
    global _runner  # noqa: PLW0603
    if runner is None:
        _runner = _default_subprocess_runner
    else:
        _runner = runner


def get_runner() -> SubprocessRunner:
    """Return the currently active subprocess runner.

    Useful for introspection during tests.
    """
    return _runner


def invoke_qwen(
    config: OpencodeCallConfig,
    *,
    _injected_runner: SubprocessRunner | None = None,
) -> OpencodeCallResult:
    """Invoke the Qwen model via opencode-go CLI and capture raw stdout.

    This is the main entry point for **Sub-AC 2c-2**.

    Constructs the CLI command from *config*, executes it via the
    active subprocess runner, and returns a frozen ``OpencodeCallResult``
    with the raw stdout, stderr, exit code, timing, and success flag.

    **The caller is responsible for** JSON extraction, field validation,
    and classification parsing on the returned ``stdout``.  This module
    only handles the CLI mechanics.

    Args:
        config: ``OpencodeCallConfig`` with model, context_file, timeout.
        _injected_runner: Override the subprocess runner for this single
                          call.  Prefer ``inject_runner()`` for test
                          suites; use this parameter for ad-hoc testing
                          or nested dependency injection.

    Returns:
        ``OpencodeCallResult`` — check ``result.success`` before
        consuming ``result.stdout``.

    Raises:
        ValueError: If *config.model* or *config.context_file* is empty.
        TypeError: If *config* is not an ``OpencodeCallConfig``.

    Examples:
        >>> config = OpencodeCallConfig(
        ...     model="qwen-max",
        ...     context_file="/tmp/packet.json",
        ...     timeout_seconds=60,
        ... )
        >>> result = invoke_qwen(config)
        >>> if result.success:
        ...     print(f"Output: {result.stdout[:200]}")
        ... else:
        ...     print(f"Failed: {result.error_message}")
    """
    if not isinstance(config, OpencodeCallConfig):
        raise TypeError(
            f"config must be OpencodeCallConfig, got {type(config).__name__}"
        )

    # Build the command
    command = build_opencode_command(config.model, config.context_file)

    # Resolve the runner
    runner = _injected_runner if _injected_runner is not None else _runner

    # Execute
    start = time.monotonic()
    exit_code, stdout, stderr = runner(
        command,
        config.timeout_seconds,
        config.env,
        config.workdir,
    )
    elapsed = time.monotonic() - start

    # Interpret result
    if exit_code == 0:
        return OpencodeCallResult(
            success=True,
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(elapsed, 4),
            model=config.model,
            context_file=config.context_file,
            timeout_occurred=False,
            error_message="",
        )

    # exit_code == -1 means timeout or OSError
    if exit_code == -1:
        is_timeout = "timeout" in stderr.lower() if stderr else False
        return OpencodeCallResult(
            success=False,
            exit_code=-1,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(elapsed, 4),
            model=config.model,
            context_file=config.context_file,
            timeout_occurred=True,
            error_message=(
                f"opencode-go timed out after {config.timeout_seconds}s"
                if is_timeout or not stderr
                else f"opencode-go subprocess error: {stderr[:200]}"
            ),
        )

    # Non-zero exit code from the CLI itself
    return OpencodeCallResult(
        success=False,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=round(elapsed, 4),
        model=config.model,
        context_file=config.context_file,
        timeout_occurred=False,
        error_message=(
            f"opencode-go exited with code {exit_code}: "
            f"{stderr[:200] if stderr else 'no stderr output'}"
        ),
    )
