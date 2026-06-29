"""Discord meeting bridge — wired into Runtime Architecture v2.

This adapter receives Discord interaction payloads (slash commands or mention
triggers) and routes them into the unified Runtime Architecture v2 pipeline
via ``gateway_bridge``.

Old pipeline (meeting_orchestration_pipeline / meeting_creation_dispatcher) is
deprecated.  All new meeting flows go through this single bridge.
"""

from __future__ import annotations

from typing import Any

from src.runtime_architecture_v2.gateway_bridge import (
    GatewayMeetingResult,
    GatewayMeetingTrigger,
    run_meeting_from_gateway,
)

CHANNEL_MESSAGE_WITH_SOURCE = 4
EPHEMERAL = 64


def handle_discord_meeting_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a meeting request from Discord (slash command or mention).

    Returns a Discord interaction response dict.
    """
    topic = _extract_option(payload, "topic") or _extract_option(payload, "text")
    if not topic or not topic.strip():
        return _ephemeral_error(
            "회의 주제가 비어 있습니다. /meeting topic:<주제> 형식으로 요청해 주세요."
        )

    channel_id = str(payload.get("channel_id") or "")
    user_id = _extract_user_id(payload)
    guild_id = str(payload.get("guild_id") or "1505600166676271244")
    thread_id = str(payload.get("thread_id") or channel_id)
    force_meeting = _extract_option(payload, "force_meeting") or "true"

    if force_meeting.lower() not in ("true", "1", "yes"):
        return _deferred_response("회의 의도를 감지하지 못했습니다.")

    trigger = GatewayMeetingTrigger(
        text=topic,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=guild_id,
        thread_id=thread_id,
    )

    result: GatewayMeetingResult = run_meeting_from_gateway(
        trigger,
        live_discord=True,
        create_thread=True,
    )

    if not result.success:
        return {
            "type": CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": f"❌ 회의 실행 실패: {result.error}"},
        }

    return {
        "type": CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {"content": result.summary},
    }


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


def _ephemeral_error(message: str) -> dict[str, Any]:
    return {
        "type": CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {"content": f"❌ {message}", "flags": EPHEMERAL},
    }


def _deferred_response(message: str) -> dict[str, Any]:
    return {
        "type": CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {"content": message},
    }
