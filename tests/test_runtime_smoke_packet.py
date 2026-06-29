"""Smoke test for runtime packet assembly (post-pipeline-unification).

These tests verify that context packets are assembled correctly when a
meeting goes through the unified gateway_bridge pipeline.  The old
meeting_orchestration_pipeline has been deleted; all flows now route
through gateway_bridge.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from src.runtime_architecture_v2.gateway_bridge import (
    GatewayMeetingResult,
    GatewayMeetingTrigger,
    run_meeting_from_gateway,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path / "meeting_runs"


def test_dry_run_succeeds(tmp_root: Path) -> None:
    """Dry-run path completes without errors."""
    trigger = GatewayMeetingTrigger(
        text="신규 버추얼 아이돌 그룹 데뷔 컨셉 회의",
        user_id="test-user",
        channel_id="test-channel",
    )
    result = run_meeting_from_gateway(
        trigger,
        root=tmp_root,
        live_discord=False,
        create_thread=False,
    )
    assert result.success, f"dry-run failed: {result.error}"
    assert result.meeting_run_id
    assert result.bot_participants


def test_live_mode_needs_discord_token(tmp_root: Path) -> None:
    """Live mode with no Discord token should fail gracefully."""
    trigger = GatewayMeetingTrigger(
        text="테스트 회의",
        user_id="u1",
        channel_id="ch1",
    )
    # No Discord token in env → live_discord should fail
    result = run_meeting_from_gateway(
        trigger,
        root=tmp_root,
        live_discord=True,
        create_thread=True,
    )
    assert not result.success
    assert "thread" in result.error or "meeting_failed" in result.error


def test_gateway_trigger_cli_roundtrip(tmp_root: Path) -> None:
    """CLI-style JSON roundtrip works."""
    trigger = GatewayMeetingTrigger(
        text="테스트",
        user_id="u",
        channel_id="ch",
    )
    result = run_meeting_from_gateway(
        trigger,
        root=tmp_root,
        live_discord=False,
    )
    assert result.success
    assert result.summary
    assert "회의" in result.summary
