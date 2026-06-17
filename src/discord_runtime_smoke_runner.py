"""Runtime smoke runner for Discord application-command interactions."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from src.runtime_live_adapters import (
    RuntimeLiveAdapterConfig,
    create_runtime_smoke_live_dependencies,
    load_runtime_live_config,
    load_runtime_live_env,
)
from src.runtime_smoke_packet import RuntimeSmokeConfig, run_runtime_smoke_packet

SmokeRunner = Callable[..., Any]
DependencyFactory = Callable[[RuntimeLiveAdapterConfig], Any]
HttpJsonRequest = Callable[..., tuple[int, dict[str, Any]]]


def run_runtime_smoke_for_interaction(
    payload: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    smoke_runner: SmokeRunner = run_runtime_smoke_packet,
    dependency_factory: DependencyFactory = create_runtime_smoke_live_dependencies,
) -> dict[str, Any]:
    """Run the live runtime smoke pipeline for one Discord interaction payload."""

    source_env = load_runtime_live_env(env)
    live_config = load_runtime_live_config(source_env)
    normalized_payload = _normalize_interaction_payload(payload, source_env)
    result = smoke_runner(
        payload=normalized_payload,
        config=RuntimeSmokeConfig(
            meetings_root=source_env.get("AI_AGENT_MEETINGS_ROOT", "meetings"),
            workdir=live_config.workdir or os.getcwd(),
            qwen_model=live_config.qwen_model,
            glm_model=live_config.glm_model,
            openclaw_action_type=source_env.get(
                "AI_AGENT_OPENCLAW_ACTION_TYPE",
                "diagnostic_read",
            ),
            openclaw_risk_level=source_env.get(
                "AI_AGENT_OPENCLAW_RISK_LEVEL",
                "high",
            ),
            openclaw_target=source_env.get(
                "AI_AGENT_OPENCLAW_TARGET",
                "runtime-smoke",
            ),
            openclaw_expected_duration_seconds=float(
                source_env.get("AI_AGENT_OPENCLAW_EXPECTED_DURATION", "5.0")
            ),
        ),
        dependencies=dependency_factory(live_config),
    )
    return _result_to_report(result)


def _normalize_interaction_payload(
    payload: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    normalized = dict(payload)
    channel_id = str(normalized.get("channel_id") or env.get("DISCORD_HOME_CHANNEL") or "")
    normalized.setdefault("channel_id", channel_id)
    normalized.setdefault("thread_id", str(normalized.get("thread_id") or channel_id))
    if "member" not in normalized and "user" not in normalized:
        normalized["member"] = {"user": {"id": "runtime-smoke"}}
    return normalized


def _result_to_report(result: Any) -> dict[str, Any]:
    data = {
        key: getattr(result, key)
        for key in [
            "success",
            "stage",
            "error",
            "meeting_id",
            "context_packet_path",
            "discord_thread_message_id",
            "discord_cross_post_message_id",
            "qwen_success",
            "glm_success",
            "openclaw_state",
            "openclaw_error",
        ]
        if hasattr(result, key)
    }
    data["ok"] = bool(data.pop("success", False))
    return data


def send_interaction_completion_followup(
    payload: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    fallback_application_id: str = "",
    http_json_request: HttpJsonRequest | None = None,
) -> dict[str, Any]:
    """Complete Discord's deferred interaction response with a result summary.

    Discord shows a slash command as "thinking" after a type=5 deferred
    response until the original interaction response is patched or a follow-up
    is posted.  The interaction webhook token authorizes this endpoint, so no
    bot Authorization header is required.
    """

    application_id = str(payload.get("application_id") or fallback_application_id or "")
    token = str(payload.get("token") or "")
    if not application_id or not token:
        return {"ok": False, "skipped": True, "error": "missing application_id or token"}

    request = http_json_request or _default_http_json_request
    url = (
        "https://discord.com/api/v10/webhooks/"
        f"{application_id}/{token}/messages/@original"
    )
    status, response = request(
        url=url,
        method="PATCH",
        body=json.dumps(
            {"content": _build_interaction_completion_content(report)},
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=20,
    )
    return {"ok": 200 <= status < 300, "status": status, "response": response}


def _build_interaction_completion_content(report: Mapping[str, Any]) -> str:
    if not bool(report.get("ok")):
        stage = str(report.get("stage") or "unknown")
        error = str(report.get("error") or "unknown error")
        return f"회의 실행 실패: {stage}\n{error}"

    meeting_id = str(report.get("meeting_id") or "meeting")
    qwen = "성공" if report.get("qwen_success") else "실패"
    glm = "성공" if report.get("glm_success") else "실패"
    openclaw = str(report.get("openclaw_state") or "unknown")
    return (
        f"회의 실행 완료: {meeting_id}\n"
        f"Qwen: {qwen} / GLM: {glm} / OpenClaw: {openclaw}"
    )


def _default_http_json_request(
    *,
    url: str,
    method: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"error": raw}
        return exc.code, data


__all__ = ["run_runtime_smoke_for_interaction", "send_interaction_completion_followup"]
