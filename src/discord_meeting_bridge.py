"""Discord slash-command bridge into the meeting orchestration pipeline.

This adapter is still pure and testable: it receives a parsed Discord
interaction payload from ``handler_router``, calls the integrated meeting
pipeline, and returns a Discord interaction response dict.  Live HTTP posting
is handled by outer Discord infrastructure.
"""

from __future__ import annotations

from typing import Any

from src.append_only_log import AppendOnlyDecisionLog
from src.handler_router import HandlerRegistry, create_default_registry
from src.knowledge_retrieval_service import KnowledgeItem
from src.meeting_creation_dispatcher import OrchestratorCallable
from src.meeting_orchestration_pipeline import (
    MeetingPipelineRequest,
    process_meeting_request,
)
from src.meeting_trigger import MeetingConfig
from src.priority_queue import PriorityMeetingQueue

CHANNEL_MESSAGE_WITH_SOURCE = 4
EPHEMERAL = 64


def build_meeting_handler(
    *,
    queue: PriorityMeetingQueue,
    knowledge_items: tuple[KnowledgeItem, ...] = (),
    decision_log: AppendOnlyDecisionLog | None = None,
    meetings_root: str | None = None,
    config: MeetingConfig | None = None,
    orchestrator: OrchestratorCallable | None = None,
):
    """Build a Discord handler callable for the ``/meeting`` command."""

    def handle(payload: dict[str, Any]) -> dict[str, Any]:
        topic = _extract_option(payload, "topic") or _extract_option(payload, "text")
        if not topic or not topic.strip():
            return _ephemeral_error("회의 주제가 비어 있습니다. /meeting topic:<주제> 형식으로 요청해 주세요.")

        channel_id = str(payload.get("channel_id") or "")
        thread_id = str(payload.get("thread_id") or channel_id)
        result_channel_id = _extract_option(payload, "result_channel_id") or channel_id
        request = MeetingPipelineRequest(
            text=topic,
            user_id=_extract_user_id(payload),
            channel_id=channel_id,
            thread_id=thread_id,
            guild_id=str(payload.get("guild_id") or ""),
            result_channel_id=result_channel_id,
            created_at=_created_at_from_payload(payload),
            force_meeting_intent=True,
        )
        result = process_meeting_request(
            request,
            queue=queue,
            knowledge_items=knowledge_items,
            decision_log=decision_log,
            meetings_root=meetings_root,
            config=config,
            orchestrator=orchestrator,
        )
        if not result.success:
            return _ephemeral_error(result.error or "회의 생성에 실패했습니다.")

        assert result.delivery_plan is not None
        assert result.queued_item is not None
        status = "즉시 시작" if result.queued_item.meeting_id in queue.running_ids else "대기열 등록"
        content = f"{result.delivery_plan.primary.content}\n상태: {status}\n회의 ID: {result.queued_item.meeting_id}"
        return {"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": {"content": content}}

    return handle


def build_meeting_registry(
    *,
    queue: PriorityMeetingQueue,
    knowledge_items: tuple[KnowledgeItem, ...] = (),
    decision_log: AppendOnlyDecisionLog | None = None,
    meetings_root: str | None = None,
    config: MeetingConfig | None = None,
    orchestrator: OrchestratorCallable | None = None,
) -> HandlerRegistry:
    """Create the default Discord registry with the meeting handler wired in."""

    registry = create_default_registry()
    registry.register(
        "meeting",
        build_meeting_handler(
            queue=queue,
            knowledge_items=knowledge_items,
            decision_log=decision_log,
            meetings_root=meetings_root,
            config=config,
            orchestrator=orchestrator,
        ),
    )
    return registry


def _extract_option(payload: dict[str, Any], name: str) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        return ""
    options = data.get("options")
    if not isinstance(options, list):
        return ""
    for option in options:
        if isinstance(option, dict) and option.get("name") == name:
            value = option.get("value")
            return str(value) if value is not None else ""
    return ""


def _extract_user_id(payload: dict[str, Any]) -> str:
    member = payload.get("member")
    if isinstance(member, dict):
        user = member.get("user")
        if isinstance(user, dict) and user.get("id"):
            return str(user["id"])
    user = payload.get("user")
    if isinstance(user, dict) and user.get("id"):
        return str(user["id"])
    return "unknown-user"


def _created_at_from_payload(payload: dict[str, Any]) -> int:
    raw = payload.get("id")
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _ephemeral_error(message: str) -> dict[str, Any]:
    return {
        "type": CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {"content": f"❌ {message}", "flags": EPHEMERAL},
    }


__all__ = ["build_meeting_handler", "build_meeting_registry"]
