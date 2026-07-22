"""Smoke test for runtime packet assembly (post-pipeline-unification).

These tests verify that context packets are assembled correctly when a
meeting goes through the unified gateway_bridge pipeline.  The old
meeting_orchestration_pipeline has been deleted; all flows now route
through gateway_bridge.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.runtime_architecture_v2.gateway_bridge import (
    GatewayMeetingTrigger,
    classify_meeting_intent,
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


def test_live_mode_needs_discord_token(
    tmp_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live mode with no Discord token should fail gracefully."""
    monkeypatch.setattr(
        "src.runtime_architecture_v2.gateway_bridge._build_profile_env",
        lambda _profile: {},
    )
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


def test_gateway_provider_error_falls_back_to_deterministic_live_projection(
    tmp_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider failures should still create a live deterministic meeting thread."""

    calls: list[dict[str, Any]] = []

    def fake_pilot(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                ok=False,
                meeting_run=SimpleNamespace(meeting_run_id="failed-provider-run"),
                meeting_thread_id="",
                error="hermes_provider_error: HTTP 503",
            )
        return SimpleNamespace(
            ok=True,
            meeting_run=SimpleNamespace(meeting_run_id="fallback-run"),
            meeting_thread_id="thread-123",
            final_report="",
            bot_participants=("ceo_coordinator", "content_lead"),
            rounds_completed=2,
            projection_messages_posted=12,
        )

    monkeypatch.setattr(
        "src.runtime_architecture_v2.gateway_bridge.run_phase14_multi_bot_pilot",
        fake_pilot,
    )
    monkeypatch.setattr(
        "src.runtime_architecture_v2.gateway_bridge._build_profile_env",
        lambda _profile: {"DISCORD_BOT_TOKEN": "test-token"},
    )

    trigger = GatewayMeetingTrigger(
        text="쇼츠 컨텐츠 마케팅 회의 시작하자",
        user_id="u1",
        channel_id="ch1",
    )
    result = run_meeting_from_gateway(
        trigger,
        root=tmp_root,
        live_discord=True,
        create_thread=True,
    )

    assert result.success is True
    assert result.meeting_run_id == "fallback-run"
    assert result.thread_id == "thread-123"
    assert "deterministic fallback" in result.summary
    assert len(calls) == 2
    assert calls[0]["live_bot_roles_override"]
    assert calls[0]["fake_bot_roles_override"] == ()
    assert calls[1]["live_bot_roles_override"] == ()
    assert calls[1]["fake_bot_roles_override"] == calls[0]["live_bot_roles_override"]
    assert calls[1]["max_live_workers"] == 0


def test_explicit_meeting_command_bypasses_keyword_intent_gate(
    tmp_root: Path,
) -> None:
    trigger = GatewayMeetingTrigger(
        text="신제품 아이디어",
        user_id="u1",
        channel_id="ch1",
    )

    result = run_meeting_from_gateway(
        trigger,
        root=tmp_root,
        live_discord=False,
        create_thread=False,
        require_meeting_intent=False,
    )

    assert result.success is True


def test_gateway_trigger_cli_roundtrip(tmp_root: Path) -> None:
    """CLI-style JSON roundtrip works."""
    trigger = GatewayMeetingTrigger(
        text="회의 테스트",  # must include meeting keyword for intent gate
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


# ── Intent classifier tests ────────────────────────────────────────────────


def test_intent_explicit_meeting():
    assert classify_meeting_intent("회의 열어줘")
    assert classify_meeting_intent("meeting now")
    assert classify_meeting_intent("미팅 하자")
    assert classify_meeting_intent("논의 필요")
    assert classify_meeting_intent("이거 토론해보자")


def test_intent_decision():
    assert classify_meeting_intent("결정해줘")
    assert classify_meeting_intent("검토 바람")
    assert classify_meeting_intent("리뷰 부탁")
    assert classify_meeting_intent("승인 요청")


def test_intent_analysis():
    assert classify_meeting_intent("분석 요청")
    assert classify_meeting_intent("전략 수립")
    assert classify_meeting_intent("기획 회의")


def test_intent_haejwo():
    assert classify_meeting_intent("콘텐츠 기획해줘")  # 3+ words
    assert classify_meeting_intent("마케팅 전략 분석해줘")


def test_intent_force_prefix():
    assert classify_meeting_intent("!아무말")


def test_intent_plain_question_rejected():
    assert not classify_meeting_intent("안녕")
    assert not classify_meeting_intent("오늘 날씨 어때")
    assert not classify_meeting_intent("지금 몇 시야")
    assert not classify_meeting_intent("해줘")  # too short for category 4
