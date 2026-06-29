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

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.runtime_architecture_v2.multi_bot import (
    MultiBotPilotResult,
    _default_discord_http_post,
    run_phase14_multi_bot_pilot,
)
from src.runtime_architecture_v2.projection import (
    DiscordLiveBoundaryPolicy,
    LiveDiscordProjectionSink,
    LiveDiscordThreadManager,
    SharedMeetingThreadProjectionPolicy,
    ProjectionPublishResult,
    ThreadCreateResult,
    _default_discord_http_post as _projection_default_post,
    _sanitize_discord_content,
)
from src.runtime_architecture_v2.discord_channels import (
    current_discord_home_channel_ids_by_profile,
)

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

    @classmethod
    def from_discord_mention(cls, content: str, channel_id: str, user_id: str, guild_id: str = "1505600166676271244") -> GatewayMeetingTrigger:
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
    bot_roles: tuple[str, ...] = (
        "ceo_coordinator",
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "business_support_lead",
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

    boundary_policy = DiscordLiveBoundaryPolicy.current_verified()
    guild_id = boundary_policy.guild_id

    # Intent gate: skip multi-bot pipeline for trivial messages
    if not classify_meeting_intent(trigger.text):
        return GatewayMeetingResult(
            success=False,
            error="no_meeting_intent",
        )

    # Build per-profile token envs (one env per bot role)
    profile_envs: dict[str, dict[str, str]] = {}
    for role in bot_roles:
        profile = role  # The role name matches the Hermes profile name
        profile_envs[role] = _build_profile_env(profile)

    # CEO env for thread creation
    ceo_env = profile_envs.get("ceo_coordinator", {})

    # Derive a thread name from the trigger text
    thread_name = _derive_thread_name(trigger.text)

    post = http_post or _default_discord_http_post

    try:
        result: MultiBotPilotResult = run_phase14_multi_bot_pilot(
            root=root,
            mode="live-worker" if live_discord else "dry-run",
            max_live_workers=min(len(bot_roles), 2) if live_discord else 0,
            live_discord=live_discord,
            env=ceo_env,
            target_channel_id=trigger.channel_id,
            target_thread_id=trigger.thread_id,
            create_meeting_thread=create_thread,
            thread_name=thread_name,
            discord_http_post=post,
        )
    except Exception as exc:
        return GatewayMeetingResult(
            success=False,
            error=f"pipeline exception: {exc}",
        )

    if not result.ok:
        return GatewayMeetingResult(
            success=False,
            meeting_run_id=result.meeting_run.meeting_run_id,
            error=result.error or "meeting_failed",
        )

    # Build summary of what happened
    summary = (
        f"회의 완료: {thread_name}\n"
        f"참여자: {', '.join(BOT_PERSONA_DISPLAY.get(r, r) for r in result.bot_participants)}\n"
        f"라운드: {result.rounds_completed}\n"
        f"발언: {result.projection_messages_posted}건\n"
        f"스레드: {'생성됨' if result.meeting_thread_id else '미생성'}"
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
    "business_support_lead": "사업지원 팀장",
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
    if "해줘" in t and len(t.split()) >= 3:
        return True

    return False


