"""Gateway bridge: Hermes Gateway ↔ AI_Agent Runtime Architecture v2.

Unified integration point. When a user mentions a bot with a meeting request,
this module translates the Discord trigger into a Runtime Architecture v2
MeetingRun, executes the multi-bot protocol with live Discord projection, and
returns structured results.

Design (Option A):
    Hermes Gateway (대표 profile)
        → meeting_trigger skill
        → gateway_bridge.run_meeting_from_gateway()
            → multi_bot.run_phase14_multi_bot_pilot()
                → LiveDiscordThreadManager → create thread
                → route_bot_projection() → each team lead bot posts in thread
                → HermesProviderWorkerRunner → live worker execution
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from src.runtime_architecture_v2.multi_bot import (
    MultiBotPilotResult,
    _default_discord_http_post,
    run_phase14_multi_bot_pilot,
)
from src.runtime_architecture_v2.store import MeetingRunStore

# ── Gateway trigger shape ────────────────────────────────────────────────


@dataclass(frozen=True)
class GatewayMeetingTrigger:
    """Normalised trigger from any Gateway platform (Discord, Telegram, etc.)."""

    text: str
    user_id: str
    channel_id: str
    guild_id: str = ""
    thread_id: str = ""
    platform: str = "discord"
    priority: str = "P1"
    invocation_id: str = ""

    @classmethod
    def from_discord_mention(
        cls,
        content: str,
        channel_id: str,
        user_id: str,
        guild_id: str = "1505600166676271244",
    ) -> GatewayMeetingTrigger:
        return cls(
            text=content,
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
        )


@dataclass(frozen=True)
class GatewayMeetingResult:
    """Structured result returned to the Gateway caller."""

    success: bool
    meeting_run_id: str = ""
    thread_id: str = ""
    thread_name: str = ""
    summary: str = ""
    bot_participants: tuple[str, ...] = ()
    projection_messages_posted: int = 0
    error: str = ""


# ── Profile token loader ─────────────────────────────────────────────────

def _load_profile_token(profile: str) -> str:
    """Load DISCORD_BOT_TOKEN from a Hermes profile .env file."""
    env_path = Path.home() / ".hermes" / "profiles" / profile / ".env"
    if not env_path.exists():
        return ""
    with open(env_path) as f:
        for line in f:
            parts = line.strip().split("=", 1)
            if len(parts) == 2 and parts[0].strip() == "DISCORD_BOT_TOKEN":
                return parts[1].strip().strip('"').strip("'")
    return ""


def _build_profile_env(profile: str) -> dict[str, str]:
    """Return a DISCORD_BOT_TOKEN env dict for one profile."""
    token = _load_profile_token(profile)
    return {"DISCORD_BOT_TOKEN": token} if token else {}


# ── Main gateway entry point ─────────────────────────────────────────────

def run_meeting_from_gateway(
    trigger: GatewayMeetingTrigger,
    *,
    root: str | Path | None = None,
    live_discord: bool = True,
    create_thread: bool = True,
    require_meeting_intent: bool = True,
    bot_roles: tuple[str, ...] = (
        "ceo_coordinator",
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "validation_audit",
    ),
    rounds: int = 2,
    http_post: Callable[..., Mapping[str, object]] | None = None,
) -> GatewayMeetingResult:
    """Run a full multi-bot meeting from a Gateway trigger.

    Returns a structured result suitable for the Gateway to report back to
    the user.  When ``live_discord=True``, each team lead bot posts in the
    shared meeting thread using its own Discord bot token.
    """
    if root is None:
        root = Path(__file__).resolve().parents[2] / "runtime" / "meeting_runs"

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    # Intent gate: skip multi-bot pipeline for trivial messages
    if require_meeting_intent and not classify_meeting_intent(trigger.text):
        return GatewayMeetingResult(
            success=False,
            error="no_meeting_intent",
        )

    # CEO env is used only for thread creation. Individual bot projections
    # load their own profile tokens from the role→profile map in multi_bot.py.
    ceo_env = _build_profile_env("aicompanyceo")
    if live_discord and not ceo_env.get("DISCORD_BOT_TOKEN"):
        return GatewayMeetingResult(
            success=False,
            error="thread creation requires aicompanyceo DISCORD_BOT_TOKEN",
        )

    invocation_key, expires_after_seconds = _gateway_invocation_key(trigger)
    store = MeetingRunStore(root)
    reserved, existing = store.reserve_gateway_invocation(
        invocation_key,
        created_at_epoch=time.time(),
        expires_after_seconds=expires_after_seconds,
    )
    if not reserved:
        return _result_from_invocation(existing)

    # Derive a thread name from the trigger text
    thread_name = _derive_thread_name(trigger.text)

    post = http_post or _default_discord_http_post

    try:
        result: MultiBotPilotResult = run_phase14_multi_bot_pilot(
            root=root,
            mode="live-worker" if live_discord else "dry-run",
            max_live_workers=len(bot_roles) if live_discord else 0,
            live_discord=live_discord,
            env=ceo_env,
            target_channel_id=trigger.channel_id,
            target_thread_id=trigger.thread_id,
            create_meeting_thread=create_thread,
            thread_name=thread_name,
            trigger_text=trigger.text,
            discord_http_post=post,
            live_bot_roles_override=bot_roles,
            fake_bot_roles_override=(),
            user_id=trigger.user_id,
            guild_id=trigger.guild_id,
            priority=trigger.priority,
            platform=trigger.platform,
            invocation_id=trigger.invocation_id,
            idempotency_key=invocation_key,
        )
    except Exception as exc:
        return _complete_gateway_invocation(store, invocation_key, GatewayMeetingResult(
            success=False,
            error=f"pipeline exception: {exc}",
        ))

    if not result.ok:
        if live_discord and _is_provider_failure(result.error):
            fallback = _run_deterministic_live_fallback(
                root=root,
                trigger=trigger,
                create_thread=create_thread,
                bot_roles=bot_roles,
                ceo_env=ceo_env,
                thread_name=thread_name,
                http_post=post,
                existing_thread_id=result.meeting_thread_id,
            )
            if fallback.ok:
                return _complete_gateway_invocation(store, invocation_key, _gateway_success_result(
                    fallback,
                    thread_name=thread_name,
                    fallback_reason=result.error or "provider failure",
                ))
            return _complete_gateway_invocation(store, invocation_key, GatewayMeetingResult(
                success=False,
                meeting_run_id=fallback.meeting_run.meeting_run_id,
                error=f"provider fallback failed: {fallback.error or 'meeting_failed'}",
            ))
        return _complete_gateway_invocation(store, invocation_key, GatewayMeetingResult(
            success=False,
            meeting_run_id=result.meeting_run.meeting_run_id,
            error=result.error or "meeting_failed",
        ))

    return _complete_gateway_invocation(
        store,
        invocation_key,
        _gateway_success_result(result, thread_name=thread_name),
    )


def _run_deterministic_live_fallback(
    *,
    root: Path,
    trigger: GatewayMeetingTrigger,
    create_thread: bool,
    bot_roles: tuple[str, ...],
    ceo_env: Mapping[str, str],
    thread_name: str,
    http_post: Callable[..., Mapping[str, object]],
    existing_thread_id: str = "",
) -> MultiBotPilotResult:
    """Post deterministic role messages when provider workers are unavailable."""

    target_thread_id = existing_thread_id or trigger.thread_id
    return run_phase14_multi_bot_pilot(
        root=root,
        mode="live-worker",
        max_live_workers=0,
        live_discord=True,
        env=ceo_env,
        target_channel_id=trigger.channel_id,
        target_thread_id=target_thread_id,
        create_meeting_thread=create_thread and not target_thread_id,
        thread_name=thread_name,
        trigger_text=trigger.text,
        discord_http_post=http_post,
        live_bot_roles_override=(),
        fake_bot_roles_override=bot_roles,
        user_id=trigger.user_id,
        guild_id=trigger.guild_id,
        priority=trigger.priority,
        platform=trigger.platform,
        invocation_id=trigger.invocation_id,
    )


def _gateway_invocation_key(
    trigger: GatewayMeetingTrigger,
) -> tuple[str, float | None]:
    if trigger.invocation_id:
        identity = f"exact\0{trigger.platform}\0{trigger.invocation_id}"
        expires_after_seconds = None
    else:
        normalized_text = " ".join(trigger.text.casefold().split())
        identity = "\0".join(
            (
                "fallback",
                trigger.platform,
                trigger.guild_id,
                trigger.channel_id,
                trigger.thread_id,
                trigger.user_id,
                normalized_text,
            )
        )
        expires_after_seconds = 90.0
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"gateway_{digest}", expires_after_seconds


def _complete_gateway_invocation(
    store: MeetingRunStore,
    invocation_key: str,
    result: GatewayMeetingResult,
) -> GatewayMeetingResult:
    store.complete_gateway_invocation(
        invocation_key,
        {
            "success": result.success,
            "meeting_run_id": result.meeting_run_id,
            "thread_id": result.thread_id,
            "thread_name": result.thread_name,
            "summary": result.summary,
            "bot_participants": list(result.bot_participants),
            "projection_messages_posted": result.projection_messages_posted,
            "error": result.error,
        },
    )
    return result


def _result_from_invocation(payload: Mapping[str, object]) -> GatewayMeetingResult:
    if not payload.get("completed"):
        return GatewayMeetingResult(success=False, error="meeting_in_progress")
    participants = payload.get("bot_participants") or []
    return GatewayMeetingResult(
        success=bool(payload.get("success")),
        meeting_run_id=str(payload.get("meeting_run_id") or ""),
        thread_id=str(payload.get("thread_id") or ""),
        thread_name=str(payload.get("thread_name") or ""),
        summary=str(payload.get("summary") or ""),
        bot_participants=(
            tuple(str(role) for role in participants)
            if isinstance(participants, list)
            else ()
        ),
        projection_messages_posted=int(payload.get("projection_messages_posted") or 0),
        error=str(payload.get("error") or ""),
    )


def _is_provider_failure(error: str) -> bool:
    normalized = (error or "").lower()
    return any(
        marker in normalized
        for marker in (
            "hermes_provider_error",
            "provider",
            "http 503",
            "inference is temporarily unavailable",
            "failover_exhausted",
        )
    )


def _gateway_success_result(
    result: MultiBotPilotResult,
    *,
    thread_name: str,
    fallback_reason: str = "",
) -> GatewayMeetingResult:
    # Phase 32: gateway response does not claim a final report was generated.
    # The default meeting thread contains only team-lead discussion messages.
    # Reports, summaries, and exports are on-demand actions.
    fallback_notice = (
        "\n운영 참고: provider 실패로 deterministic fallback을 사용했습니다 "
        f"({fallback_reason})."
        if fallback_reason
        else ""
    )
    summary = result.final_report or (
        f"회의 완료: {thread_name}\n"
        "참여자: "
        f"{', '.join(BOT_PERSONA_DISPLAY.get(r, r) for r in result.bot_participants)}\n"
        f"라운드: {result.rounds_completed}\n"
        f"발언: {result.projection_messages_posted}건\n"
        f"스레드: {'생성됨' if result.meeting_thread_id else '미생성'}\n"
        f"{fallback_notice}"
        f"\n"
        "필요하면 '요약해줘', '최종보고서로 정리해줘', "
        "'Notion에 저장해줘', '세컨드브레인에 넣어줘'라고 요청하세요."
    )

    return GatewayMeetingResult(
        success=True,
        meeting_run_id=result.meeting_run.meeting_run_id,
        thread_id=result.meeting_thread_id,
        thread_name=thread_name,
        summary=summary,
        bot_participants=result.bot_participants,
        projection_messages_posted=result.projection_messages_posted,
    )


# ── CLI entry point (for Hermes skill shell invocation) ───────────────────

def _derive_thread_name(text: str) -> str:
    """Create a short thread name from the user's meeting request."""
    cleaned = text.strip().replace("\n", " ")[:80]
    if len(cleaned) > 30:
        cleaned = cleaned[:30] + "..."
    return f"회의: {cleaned}"


BOT_PERSONA_DISPLAY: dict[str, str] = {
    "ceo_coordinator": "대표",
    "content_lead": "콘텐츠 팀장",
    "art_lead": "아트 팀장",
    "tech_lead": "기술 팀장",
    "marketing_lead": "마케팅 팀장",
    "validation_audit": "검증 팀장",
}


def gateway_cli_main() -> None:
    """CLI entry point for the Hermes skill.

    Expects JSON on stdin with keys: text, channel_id, user_id, [guild_id].

    Outputs JSON result to stdout.
    """
    import sys

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    trigger = GatewayMeetingTrigger(
        text=str(data.get("text", "")),
        user_id=str(data.get("user_id", "gateway-user")),
        channel_id=str(data.get("channel_id", "")),
        guild_id=str(data.get("guild_id", "1505600166676271244")),
        thread_id=str(data.get("thread_id", "")),
    )

    result = run_meeting_from_gateway(trigger, live_discord=True)

    print(json.dumps({
        "success": result.success,
        "meeting_run_id": result.meeting_run_id,
        "thread_id": result.thread_id,
        "thread_name": result.thread_name,
        "summary": result.summary,
        "projection_messages_posted": result.projection_messages_posted,
        "error": result.error,
    }, ensure_ascii=False))


if __name__ == "__main__":
    gateway_cli_main()
# ── Intent classification ─────────────────────────────────────────────────

_MEETING_KEYWORDS: tuple[str, ...] = (
    "회의", "meeting", "미팅", "논의", "토론", "상의", "협의",
)
_DECISION_KEYWORDS: tuple[str, ...] = (
    "결정", "판단", "검토", "리뷰", "review", "승인", "확정",
)
_ANALYSIS_KEYWORDS: tuple[str, ...] = (
    "분석", "전략", "기획", "평가", "진단",
)
_FORCE_PREFIX: str = "!"


def classify_meeting_intent(text: str) -> bool:
    """Return True if the message warrants a multi-bot meeting.

    Covers 5 trigger categories (see meeting-trigger skill).  Short
    one-line questions without any meeting keyword are left for the
    Gateway to answer directly.
    """
    t = text.lower()

    # "!" prefix forces execution unconditionally
    if t.startswith(_FORCE_PREFIX):
        return True

    # Category 1: explicit meeting request
    if any(kw in t for kw in _MEETING_KEYWORDS):
        return True

    # Category 2: decision request
    if any(kw in t for kw in _DECISION_KEYWORDS):
        return True

    # Category 3: analysis / planning
    if any(kw in t for kw in _ANALYSIS_KEYWORDS):
        return True

    # Category 4: "해줘" + substantive target (not just chitchat)
    return bool("해줘" in t and len(t.split()) >= 3)


