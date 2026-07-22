from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.runtime_architecture_v2.gateway_bridge import GatewayMeetingResult
from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext
from src.runtime_architecture_v2.meeting_commands import (
    run_meeting_report,
    run_meeting_start,
)
from src.runtime_architecture_v2.on_demand_exports import (
    OnDemandExportResult,
    OnDemandExportType,
)
from src.runtime_architecture_v2.schemas import MeetingRun
from src.runtime_architecture_v2.store import MeetingRunStore

_CEO_CHANNEL_ID = "1505600167221526621"
_GUILD_ID = "1505600166676271244"


def _context(**overrides: str) -> HermesCommandContext:
    values = {
        "platform": "discord",
        "chat_id": _CEO_CHANNEL_ID,
        "thread_id": "",
        "guild_id": _GUILD_ID,
        "user_id": "user-1",
        "session_id": "session-1",
        "invocation_id": "interaction-1",
        "profile": "aicompanyceo",
    }
    values.update(overrides)
    return HermesCommandContext(**values)


def _linked_meeting(root: Path, thread_id: str = "thread-1") -> MeetingRun:
    run = MeetingRun.create(
        meeting_run_id="meeting-linked",
        trigger_text="신제품 회의",
        user_id="user-1",
        channel_id=_CEO_CHANNEL_ID,
        thread_id="",
        guild_id=_GUILD_ID,
    )
    run = replace(run, metadata={"discord_thread_id": thread_id})
    MeetingRunStore(root).save_meeting_run(run)
    return run


@pytest.mark.parametrize(
    ("raw_request", "context", "status"),
    [
        ("   ", _context(), "invalid_request"),
        ("신제품 아이디어", _context(platform="telegram"), "discord_only"),
        ("신제품 아이디어", _context(chat_id=""), "missing_channel"),
    ],
)
def test_meeting_start_rejects_invalid_input_without_calling_gateway(
    tmp_path: Path,
    raw_request: str,
    context: HermesCommandContext,
    status: str,
) -> None:
    called = False

    def gateway_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError((args, kwargs))

    result = run_meeting_start(
        raw_request,
        context=context,
        root=tmp_path,
        gateway_runner=gateway_runner,
    )

    assert result.ok is False
    assert result.status == status
    assert called is False


@pytest.mark.parametrize(
    ("context", "expected_channel", "expected_thread", "expected_create"),
    [
        (_context(), _CEO_CHANNEL_ID, "", True),
        (
            _context(chat_id="thread-1", thread_id="thread-1"),
            _CEO_CHANNEL_ID,
            "thread-1",
            False,
        ),
    ],
)
def test_meeting_start_calls_runtime_v2_with_current_discord_context(
    tmp_path: Path,
    context: HermesCommandContext,
    expected_channel: str,
    expected_thread: str,
    expected_create: bool,
) -> None:
    if context.thread_id:
        _linked_meeting(tmp_path, context.thread_id)
    captured = {}

    def gateway_runner(trigger, **kwargs):
        captured["trigger"] = trigger
        captured.update(kwargs)
        return GatewayMeetingResult(
            success=True,
            meeting_run_id="meeting-1",
            thread_id="thread-created",
            thread_name="회의: 신제품 아이디어",
            projection_messages_posted=12,
        )

    result = run_meeting_start(
        "  신제품 아이디어  ",
        context=context,
        root=tmp_path,
        gateway_runner=gateway_runner,
    )

    assert result.ok is True
    assert result.status == "started"
    assert result.meeting_run_id == "meeting-1"
    assert result.thread_id == "thread-created"
    assert "<#thread-created>" in result.message
    assert captured["trigger"].text == "신제품 아이디어"
    assert captured["trigger"].user_id == "user-1"
    assert captured["trigger"].channel_id == expected_channel
    assert captured["trigger"].thread_id == expected_thread
    assert captured["trigger"].guild_id == _GUILD_ID
    assert captured["trigger"].invocation_id == "interaction-1"
    assert captured["root"] == tmp_path
    assert captured["live_discord"] is True
    assert captured["create_thread"] is expected_create
    assert captured["require_meeting_intent"] is False


def test_meeting_start_rejects_non_ceo_parent_before_gateway(tmp_path: Path) -> None:
    called = False

    def gateway_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError((args, kwargs))

    result = run_meeting_start(
        "신제품 아이디어",
        context=_context(chat_id="wrong-parent"),
        root=tmp_path,
        gateway_runner=gateway_runner,
    )

    assert result.status == "invalid_channel"
    assert called is False


def test_meeting_start_rejects_unlinked_thread_before_gateway(tmp_path: Path) -> None:
    called = False

    def gateway_runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError((args, kwargs))

    result = run_meeting_start(
        "신제품 아이디어",
        context=_context(chat_id="unknown-thread", thread_id="unknown-thread"),
        root=tmp_path,
        gateway_runner=gateway_runner,
    )

    assert result.status == "unlinked_thread"
    assert called is False


def test_meeting_start_sanitizes_gateway_failure(tmp_path: Path) -> None:
    def gateway_runner(*_args, **_kwargs):
        return GatewayMeetingResult(
            success=False,
            error="provider token=secret /home/ubuntu/private",
        )

    result = run_meeting_start(
        "신제품 아이디어",
        context=_context(),
        root=tmp_path,
        gateway_runner=gateway_runner,
    )

    assert result.ok is False
    assert result.status == "start_failed"
    assert "secret" not in result.message
    assert "/home/ubuntu" not in result.message


def test_meeting_report_requires_a_linked_discord_thread(tmp_path: Path) -> None:
    outside = run_meeting_report("", context=_context(), root=tmp_path)
    missing = run_meeting_report(
        "",
        context=_context(chat_id="thread-x", thread_id="thread-x"),
        root=tmp_path,
    )

    assert outside.status == "thread_only"
    assert missing.status == "meeting_not_found"


@pytest.mark.parametrize(
    ("raw_request", "expected"),
    [
        ("", OnDemandExportType.FINAL_REPORT),
        ("브리핑해줘", OnDemandExportType.SUMMARY),
        ("간단 요약", OnDemandExportType.SUMMARY),
        ("summary", OnDemandExportType.SUMMARY),
        ("합의와 결론만", OnDemandExportType.AGREEMENT),
        ("agreement", OnDemandExportType.AGREEMENT),
        ("할 일 정리", OnDemandExportType.ACTION_ITEMS),
        ("액션 아이템", OnDemandExportType.ACTION_ITEMS),
        ("todo action", OnDemandExportType.ACTION_ITEMS),
        ("리스크 중심으로 정리", OnDemandExportType.FINAL_REPORT),
    ],
)
def test_meeting_report_resolves_current_meeting_and_classifies_request(
    tmp_path: Path,
    raw_request: str,
    expected: OnDemandExportType,
) -> None:
    run = _linked_meeting(tmp_path)
    captured = {}

    def exporter(root, meeting_run_id, export_type):
        captured.update(
            root=root,
            meeting_run_id=meeting_run_id,
            export_type=export_type,
        )
        return OnDemandExportResult(
            export_type=str(export_type),
            meeting_run_id=meeting_run_id,
            content="요청된 회의 보고 내용",
        )

    result = run_meeting_report(
        raw_request,
        context=_context(chat_id="thread-1", thread_id="thread-1"),
        root=tmp_path,
        exporter=exporter,
    )

    assert result.ok is True
    assert result.status == "reported"
    assert result.meeting_run_id == run.meeting_run_id
    assert "요청된 회의 보고 내용" in result.message
    assert captured == {
        "root": tmp_path,
        "meeting_run_id": run.meeting_run_id,
        "export_type": expected,
    }


def test_meeting_report_sanitizes_export_failure(tmp_path: Path) -> None:
    _linked_meeting(tmp_path)

    def exporter(*_args, **_kwargs):
        raise RuntimeError("token=secret /home/ubuntu/private")

    result = run_meeting_report(
        "보고서",
        context=_context(chat_id="thread-1", thread_id="thread-1"),
        root=tmp_path,
        exporter=exporter,
    )

    assert result.ok is False
    assert result.status == "report_failed"
    assert "secret" not in result.message
    assert "/home/ubuntu" not in result.message


def test_meeting_report_compacts_at_complete_section_boundary(tmp_path: Path) -> None:
    run = _linked_meeting(tmp_path)
    conclusion = "결론 근거 " * 40
    long_evidence = "세부 근거 " * 500

    def exporter(root, meeting_run_id, export_type):
        return OnDemandExportResult(
            export_type=str(export_type),
            meeting_run_id=meeting_run_id,
            content=(
                f"# 회의 보고서\n\n## 결론\n{conclusion}\n\n"
                f"## 전체 근거\n{long_evidence}\n"
            ),
        )

    result = run_meeting_report(
        "보고서",
        context=_context(chat_id="thread-1", thread_id="thread-1"),
        root=tmp_path,
        exporter=exporter,
    )

    assert result.ok is True
    assert len(result.message) <= 1900
    assert conclusion.strip() in result.message
    assert " ..." not in result.message
    assert "전체 보고서" in result.message
    assert f"runtime/meeting_runs/{run.meeting_run_id}/reports/final_report.md" in result.message
