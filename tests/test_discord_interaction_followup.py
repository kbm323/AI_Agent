from __future__ import annotations

import json
from typing import Any

from src.discord_runtime_smoke_runner import send_interaction_completion_followup


def test_send_interaction_completion_followup_patches_original_response():
    calls: list[dict[str, Any]] = []

    def http_json_request(*, url, method, body, headers, timeout):
        calls.append(
            {
                "url": url,
                "method": method,
                "body": json.loads(body.decode("utf-8")),
                "headers": headers,
                "timeout": timeout,
            }
        )
        return 200, {"id": "original-response"}

    result = send_interaction_completion_followup(
        {
            "application_id": "app-1",
            "token": "interaction-token",
        },
        {
            "ok": True,
            "stage": "complete",
            "meeting_id": "meeting-1",
            "qwen_success": True,
            "glm_success": True,
            "openclaw_state": "blocked_for_approval",
        },
        http_json_request=http_json_request,
    )

    assert result == {"ok": True, "status": 200, "response": {"id": "original-response"}}
    assert calls == [
        {
            "url": "https://discord.com/api/v10/webhooks/app-1/interaction-token/messages/@original",
            "method": "PATCH",
            "body": {
                "content": "회의 실행 완료: meeting-1\nQwen: 성공 / GLM: 성공 / OpenClaw: blocked_for_approval",
            },
            "headers": {"Content-Type": "application/json"},
            "timeout": 20,
        }
    ]


def test_send_interaction_completion_followup_skips_without_token():
    called = False

    def http_json_request(**kwargs):
        nonlocal called
        called = True
        return 200, {}

    result = send_interaction_completion_followup(
        {"application_id": "app-1"},
        {"ok": True, "stage": "complete"},
        http_json_request=http_json_request,
    )

    assert result == {"ok": False, "skipped": True, "error": "missing application_id or token"}
    assert called is False


def test_send_interaction_completion_followup_reports_failure_summary():
    calls: list[dict[str, Any]] = []

    def http_json_request(*, url, method, body, headers, timeout):
        calls.append(json.loads(body.decode("utf-8")))
        return 200, {}

    result = send_interaction_completion_followup(
        {"application_id": "app-1", "token": "token-1"},
        {"ok": False, "stage": "qwen", "error": "model timeout"},
        http_json_request=http_json_request,
    )

    assert result["ok"] is True
    assert calls[0] == {"content": "회의 실행 실패: qwen\nmodel timeout"}
