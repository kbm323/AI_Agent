"""Transport-neutral Runtime v2 services for Hermes meeting commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .gateway_bridge import (
    GatewayMeetingResult,
    GatewayMeetingTrigger,
    run_meeting_from_gateway,
)
from .hermes_command_context import HermesCommandContext
from .on_demand_exports import (
    OnDemandExportResult,
    OnDemandExportType,
    run_on_demand_export,
)
from .store import MeetingRunStore

_DISCORD_MESSAGE_LIMIT = 1900
_START_FAILED = "회의를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."
_REPORT_FAILED = "회의 보고서를 만들지 못했습니다. 잠시 후 다시 시도해 주세요."

GatewayRunner = Callable[..., GatewayMeetingResult]
ExportRunner = Callable[[str | Path, str, OnDemandExportType], OnDemandExportResult]


@dataclass(frozen=True)
class MeetingCommandResult:
    """Stable command result safe for a messaging surface."""

    ok: bool
    status: str
    message: str
    meeting_run_id: str = ""
    thread_id: str = ""


def run_meeting_start(
    request: str,
    *,
    context: HermesCommandContext,
    root: str | Path,
    gateway_runner: GatewayRunner = run_meeting_from_gateway,
) -> MeetingCommandResult:
    """Start one Runtime v2 meeting from an explicit Hermes command."""

    topic = request.strip()
    if not topic:
        return MeetingCommandResult(
            ok=False,
            status="invalid_request",
            message="회의 주제를 입력해 주세요. 예: /meeting-start 신제품 아이디어",
        )
    if context.platform.casefold() != "discord":
        return MeetingCommandResult(
            ok=False,
            status="discord_only",
            message="/meeting-start는 Discord에서만 사용할 수 있습니다.",
        )
    if not context.chat_id:
        return MeetingCommandResult(
            ok=False,
            status="missing_channel",
            message=(
                "현재 Discord 채널을 확인하지 못했습니다. "
                "채널에서 다시 실행해 주세요."
            ),
        )

    trigger = GatewayMeetingTrigger(
        text=topic,
        user_id=context.user_id or "discord-user",
        channel_id=context.chat_id,
        thread_id=context.thread_id,
        platform="discord",
    )
    try:
        gateway_result = gateway_runner(
            trigger,
            root=Path(root),
            live_discord=True,
            create_thread=not bool(context.thread_id),
            require_meeting_intent=False,
        )
    except Exception:
        return MeetingCommandResult(False, "start_failed", _START_FAILED)

    if (
        not gateway_result.success
        or not gateway_result.meeting_run_id
        or not gateway_result.thread_id
    ):
        return MeetingCommandResult(False, "start_failed", _START_FAILED)

    return MeetingCommandResult(
        ok=True,
        status="started",
        message=(
            "회의를 시작했습니다.\n"
            f"스레드: <#{gateway_result.thread_id}>\n"
            f"MeetingRun: `{gateway_result.meeting_run_id}`"
        ),
        meeting_run_id=gateway_result.meeting_run_id,
        thread_id=gateway_result.thread_id,
    )


def run_meeting_report(
    request: str,
    *,
    context: HermesCommandContext,
    root: str | Path,
    exporter: ExportRunner = run_on_demand_export,
) -> MeetingCommandResult:
    """Render an on-demand report for the current linked meeting thread."""

    if context.platform.casefold() != "discord" or not context.thread_id:
        return MeetingCommandResult(
            ok=False,
            status="thread_only",
            message="/meeting-report는 연결된 회의 스레드 안에서 실행해 주세요.",
        )

    try:
        meeting_run = MeetingRunStore(root).find_by_discord_thread_id(
            context.thread_id
        )
    except (OSError, TypeError, ValueError):
        meeting_run = None
    if meeting_run is None:
        return MeetingCommandResult(
            ok=False,
            status="meeting_not_found",
            message="현재 스레드에 연결된 MeetingRun을 찾지 못했습니다.",
        )

    export_type = _classify_report_request(request)
    try:
        export_result = exporter(root, meeting_run.meeting_run_id, export_type)
    except Exception:
        return MeetingCommandResult(False, "report_failed", _REPORT_FAILED)
    if not export_result.ok or not export_result.content.strip():
        return MeetingCommandResult(False, "report_failed", _REPORT_FAILED)

    perspective = request.strip()
    request_line = f"요청: {perspective}\n" if perspective else ""
    prefix = f"MeetingRun: `{meeting_run.meeting_run_id}`\n{request_line}\n"
    report_path = (
        f"runtime/meeting_runs/{meeting_run.meeting_run_id}/reports/"
        f"{export_type.value}.md"
    )
    message = _compact_report_message(
        prefix,
        export_result.content.strip(),
        report_path,
    )
    return MeetingCommandResult(
        ok=True,
        status="reported",
        message=message,
        meeting_run_id=meeting_run.meeting_run_id,
        thread_id=context.thread_id,
    )


def _classify_report_request(request: str) -> OnDemandExportType:
    normalized = " ".join(request.casefold().split())
    if not normalized:
        return OnDemandExportType.FINAL_REPORT
    if _contains_any(normalized, ("할 일", "할일", "액션", "todo", "action")):
        return OnDemandExportType.ACTION_ITEMS
    if _contains_any(normalized, ("합의", "결론", "agreement")):
        return OnDemandExportType.AGREEMENT
    if _contains_any(
        normalized,
        ("브리핑", "요약", "간단", "brief", "summary"),
    ):
        return OnDemandExportType.SUMMARY
    return OnDemandExportType.FINAL_REPORT


def _contains_any(value: str, candidates: tuple[str, ...]) -> bool:
    return any(candidate in value for candidate in candidates)


def _compact_report_message(prefix: str, content: str, report_path: str) -> str:
    value = prefix + content
    if len(value) <= _DISCORD_MESSAGE_LIMIT:
        return value

    footer = f"\n\n전체 보고서: `{report_path}`"
    available = _DISCORD_MESSAGE_LIMIT - len(prefix) - len(footer)
    selected: list[str] = []
    used = 0
    for section in _markdown_sections(content):
        addition = section if not selected else f"\n\n{section}"
        if used + len(addition) > available:
            break
        selected.append(section)
        used += len(addition)

    compact_content = "\n\n".join(selected).strip()
    if not compact_content:
        compact_content = "보고서가 길어 전체 파일에 저장했습니다."
    return prefix + compact_content + footer


def _markdown_sections(content: str) -> tuple[str, ...]:
    sections: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.startswith("## ") and current:
            sections.append(current)
            current = []
        current.append(line)
    if current:
        sections.append(current)
    return tuple("\n".join(section).strip() for section in sections if section)


__all__ = [
    "MeetingCommandResult",
    "run_meeting_report",
    "run_meeting_start",
]
