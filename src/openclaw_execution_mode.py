"""OpenClaw execution-mode decision logic (Sub-AC 8.1).

OpenClaw tool-use actions are dispatched synchronously when the expected task
runtime is 30 seconds or less. Longer tasks are dispatched asynchronously so the
Coordinator can rely on callback/file-watch/polling notification paths without
blocking the user-facing Discord flow.
"""

from __future__ import annotations

from enum import StrEnum, unique

SYNCHRONOUS_DURATION_LIMIT_SECONDS = 30.0
"""Maximum expected duration, in seconds, for synchronous OpenClaw execution."""


@unique
class OpenClawExecutionMode(StrEnum):
    """Execution modes available to the OpenClaw dispatcher."""

    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"


def decide_execution_mode(
    expected_duration_seconds: float | int,
) -> OpenClawExecutionMode:
    """Classify an OpenClaw task as synchronous or asynchronous.

    Args:
        expected_duration_seconds: Estimated wall-clock task duration in seconds.

    Returns:
        ``SYNCHRONOUS`` for durations <= 30 seconds, otherwise ``ASYNCHRONOUS``.
    """
    if expected_duration_seconds <= SYNCHRONOUS_DURATION_LIMIT_SECONDS:
        return OpenClawExecutionMode.SYNCHRONOUS
    return OpenClawExecutionMode.ASYNCHRONOUS
