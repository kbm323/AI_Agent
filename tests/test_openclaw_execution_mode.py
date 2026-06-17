"""Tests for OpenClaw execution-mode decision logic (Sub-AC 8.1).

OpenClaw short tasks are synchronous when their expected duration is 30 seconds
or less; tasks just over 30 seconds are asynchronous.
"""

from __future__ import annotations

from src.openclaw_execution_mode import OpenClawExecutionMode, decide_execution_mode


class TestOpenClawExecutionModeDecision:
    """Classifies OpenClaw tasks by expected duration threshold."""

    def test_task_under_30_seconds_is_synchronous(self):
        """Expected duration below the threshold stays synchronous."""
        assert decide_execution_mode(29.9) is OpenClawExecutionMode.SYNCHRONOUS

    def test_task_exactly_30_seconds_is_synchronous(self):
        """The 30-second boundary is still synchronous."""
        assert decide_execution_mode(30) is OpenClawExecutionMode.SYNCHRONOUS

    def test_task_just_over_30_seconds_is_asynchronous(self):
        """Any expected duration greater than 30 seconds is asynchronous."""
        assert decide_execution_mode(30.001) is OpenClawExecutionMode.ASYNCHRONOUS
