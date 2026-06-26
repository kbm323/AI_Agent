"""Runtime smoke packet for adapter-level end-to-end verification.

This module is intentionally adapter-facing but dependency-injected.  It drives
one Discord-style meeting request through the pure meeting pipeline, delivers the
result through injected Discord callables, writes a file-based context packet,
invokes Qwen/GLM through the opencode-go wrappers, and gates one OpenClaw action.

No network or real Discord/OpenClaw/opencode-go process is required in tests;
production smoke runners can pass real adapter callables at this boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.append_only_log import AppendOnlyDecisionLog
from src.meeting_orchestration_pipeline import (
    MeetingPipelineRequest,
    process_meeting_request,
)
from src.openclaw_approval import OpenClawAction, evaluate_hitl_approval
from src.openclaw_execution_mode import decide_execution_mode
from src.opencode_glm_wrapper import GlmCallConfig, invoke_glm
from src.opencode_glm_wrapper import SubprocessRunner as GlmRunner
from src.opencode_qwen_wrapper import OpencodeCallConfig, invoke_qwen
from src.opencode_qwen_wrapper import SubprocessRunner as QwenRunner
from src.priority_queue import PriorityMeetingQueue

DiscordPoster = Callable[[str, str], Mapping[str, Any]]
OpenClawExecutor = Callable[[dict[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class RuntimeSmokeConfig:
    """Configuration for one runtime smoke pass."""

    meetings_root: str
    workdir: str
    qwen_model: str = "qwen-max"
    glm_model: str = "glm-5.1"
    qwen_timeout_seconds: float = 60.0
    glm_timeout_seconds: float = 90.0
    openclaw_action_type: str = "diagnostic_read"
    openclaw_risk_level: str = "low"
    openclaw_target: str = "runtime-smoke"
    openclaw_approval_token: str = ""
    openclaw_expected_duration_seconds: float = 5.0


@dataclass(frozen=True)
class RuntimeSmokeDependencies:
    """Injected runtime adapters used by the smoke packet."""

    post_thread: DiscordPoster
    cross_post: DiscordPoster
    qwen_runner: QwenRunner
    glm_runner: GlmRunner
    openclaw_executor: OpenClawExecutor


@dataclass(frozen=True)
class RuntimeSmokeResult:
    """Structured outcome of the runtime smoke packet."""

    success: bool
    stage: str
    error: str = ""
    meeting_id: str = ""
    context_packet_path: str = ""
    discord_thread_message_id: str = ""
    discord_cross_post_message_id: str = ""
    qwen_success: bool = False
    glm_success: bool = False
    openclaw_state: str = "not_started"
    openclaw_error: str = ""


def run_runtime_smoke_packet(
    *,
    payload: dict[str, Any],
    config: RuntimeSmokeConfig,
    dependencies: RuntimeSmokeDependencies,
) -> RuntimeSmokeResult:
    """Run one injected runtime smoke pass.

    The function stops at the first infrastructure-stage failure and returns a
    precise stage marker.  Worker/validator failures are surfaced in the result
    while preserving command/output metadata in the underlying wrapper results.
    """

    try:
        queue = PriorityMeetingQueue(max_concurrent=1)
        decision_log = AppendOnlyDecisionLog()
        pipeline_result = process_meeting_request(
            _request_from_discord_payload(payload),
            queue=queue,
            decision_log=decision_log,
            meetings_root=config.meetings_root,
        )
    except Exception:  # pragma: no cover - defensive boundary
        return RuntimeSmokeResult(
            success=False,
            stage="meeting_pipeline",
            error="smoke_pipeline_error",
        )

    if not pipeline_result.success or pipeline_result.delivery_plan is None:
        return RuntimeSmokeResult(
            success=False,
            stage="meeting_pipeline",
            error=pipeline_result.error or "meeting pipeline failed",
        )

    meeting_id = (
        pipeline_result.queued_item.meeting_id if pipeline_result.queued_item else ""
    )

    try:
        thread_receipt = dependencies.post_thread(
            pipeline_result.delivery_plan.primary.thread_id,
            pipeline_result.delivery_plan.primary.content,
        )
        cross_receipt = dependencies.cross_post(
            pipeline_result.delivery_plan.cross_post.channel_id,
            pipeline_result.delivery_plan.cross_post.content,
        )
    except Exception as exc:
        return RuntimeSmokeResult(
            success=False,
            stage="discord_delivery",
            error=str(exc),
            meeting_id=meeting_id,
        )

    try:
        context_packet_path = _write_context_packet(
            meetings_root=config.meetings_root,
            meeting_id=meeting_id,
            payload=payload,
            pipeline_result=pipeline_result,
        )
    except Exception as exc:
        return RuntimeSmokeResult(
            success=False,
            stage="context_packet",
            error=str(exc),
            meeting_id=meeting_id,
            discord_thread_message_id=str(thread_receipt.get("message_id") or ""),
            discord_cross_post_message_id=str(cross_receipt.get("message_id") or ""),
        )

    try:
        qwen_result = invoke_qwen(
            OpencodeCallConfig(
                model=config.qwen_model,
                context_file=context_packet_path,
                timeout_seconds=config.qwen_timeout_seconds,
                workdir=config.workdir,
            ),
            _injected_runner=dependencies.qwen_runner,
        )
        glm_result = invoke_glm(
            GlmCallConfig(
                model=config.glm_model,
                context_file=context_packet_path,
                timeout_seconds=config.glm_timeout_seconds,
                workdir=config.workdir,
            ),
            _injected_runner=dependencies.glm_runner,
        )
    except Exception as exc:
        return RuntimeSmokeResult(
            success=False,
            stage="worker_validation",
            error=str(exc),
            meeting_id=meeting_id,
            context_packet_path=context_packet_path,
            discord_thread_message_id=str(thread_receipt.get("message_id") or ""),
            discord_cross_post_message_id=str(cross_receipt.get("message_id") or ""),
        )

    openclaw_state = "not_started"
    openclaw_error = ""
    try:
        action = OpenClawAction(
            execution_id=f"{meeting_id}:openclaw:smoke",
            action_type=config.openclaw_action_type,
            risk_level=config.openclaw_risk_level,
            target=config.openclaw_target,
            approved_by=config.openclaw_approval_token or None,
        )
        approval = evaluate_hitl_approval(action)
    except Exception as exc:
        return RuntimeSmokeResult(
            success=False,
            stage="openclaw_gate",
            error=str(exc),
            meeting_id=meeting_id,
            context_packet_path=context_packet_path,
            discord_thread_message_id=str(thread_receipt.get("message_id") or ""),
            discord_cross_post_message_id=str(cross_receipt.get("message_id") or ""),
            qwen_success=qwen_result.success,
            glm_success=glm_result.success,
            openclaw_state="failed",
            openclaw_error=str(exc),
        )
    if not approval.allowed_to_execute:
        openclaw_state = "blocked_for_approval"
        openclaw_error = "OpenClaw approval required before execution"
    else:
        mode = decide_execution_mode(config.openclaw_expected_duration_seconds)
        try:
            receipt = dependencies.openclaw_executor(
                {
                    "execution_id": action.execution_id,
                    "action_type": action.action_type,
                    "risk_level": action.risk_level,
                    "target": action.target,
                    "mode": mode.value,
                    "meeting_id": meeting_id,
                }
            )
            openclaw_state = str(receipt.get("state") or "completed")
        except Exception:  # pragma: no cover - defensive boundary
            openclaw_state = "failed"
            openclaw_error = "smoke_openclaw_executor_error"

    overall_success = (
        qwen_result.success
        and glm_result.success
        and openclaw_state in {"completed", "blocked_for_approval"}
    )
    return RuntimeSmokeResult(
        success=overall_success,
        stage="complete" if overall_success else "runtime_execution",
        error=(
            ""
            if overall_success
            else _runtime_error(qwen_result, glm_result, openclaw_error)
        ),
        meeting_id=meeting_id,
        context_packet_path=context_packet_path,
        discord_thread_message_id=str(thread_receipt.get("message_id") or ""),
        discord_cross_post_message_id=str(cross_receipt.get("message_id") or ""),
        qwen_success=qwen_result.success,
        glm_success=glm_result.success,
        openclaw_state=openclaw_state,
        openclaw_error=openclaw_error,
    )


def _request_from_discord_payload(payload: dict[str, Any]) -> MeetingPipelineRequest:
    channel_id = str(payload.get("channel_id") or "")
    return MeetingPipelineRequest(
        text=_extract_option(payload, "topic") or _extract_option(payload, "text"),
        user_id=_extract_user_id(payload),
        channel_id=channel_id,
        thread_id=str(payload.get("thread_id") or channel_id),
        guild_id=str(payload.get("guild_id") or ""),
        result_channel_id=_extract_option(payload, "result_channel_id") or channel_id,
        created_at=_created_at_from_payload(payload),
        force_meeting_intent=_is_meeting_slash_command(payload),
    )


def _is_meeting_slash_command(payload: dict[str, Any]) -> bool:
    data = payload.get("data")
    return isinstance(data, dict) and str(data.get("name") or "").lower() == "meeting"


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


def _write_context_packet(
    *,
    meetings_root: str,
    meeting_id: str,
    payload: dict[str, Any],
    pipeline_result: Any,
) -> str:
    packet_dir = Path(meetings_root) / meeting_id
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet_path = packet_dir / "runtime_smoke_packet.json"
    packet = {
        "meeting_id": meeting_id,
        "topic": pipeline_result.intent.topic if pipeline_result.intent else "",
        "priority": (
            pipeline_result.queued_item.priority if pipeline_result.queued_item else ""
        ),
        "discord": {
            "guild_id": str(payload.get("guild_id") or ""),
            "channel_id": str(payload.get("channel_id") or ""),
            "thread_id": str(
                payload.get("thread_id") or payload.get("channel_id") or ""
            ),
        },
        "dispatch": {
            "running_ids": [
                item.meeting_id for item in pipeline_result.dispatched_items
            ],
        },
    }
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(packet_path)


def _runtime_error(qwen_result: Any, glm_result: Any, openclaw_error: str) -> str:
    parts: list[str] = []
    if not qwen_result.success:
        parts.append(f"qwen: {qwen_result.error_message}")
    if not glm_result.success:
        parts.append(f"glm: {glm_result.error_message}")
    if openclaw_error:
        parts.append(f"openclaw: {openclaw_error}")
    return "; ".join(parts)


__all__ = [
    "RuntimeSmokeConfig",
    "RuntimeSmokeDependencies",
    "RuntimeSmokeResult",
    "run_runtime_smoke_packet",
]
