from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.runtime_smoke_packet import (
    RuntimeSmokeConfig,
    RuntimeSmokeDependencies,
    run_runtime_smoke_packet,
)


def _meeting_payload() -> dict[str, Any]:
    return {
        "id": "1001",
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "thread_id": "thread-1",
        "member": {"user": {"id": "user-1"}},
        "data": {
            "name": "meeting",
            "options": [
                {
                    "name": "topic",
                    "value": "긴급 기술 회의 열어줘. API latency runtime smoke",
                },
                {"name": "result_channel_id", "value": "results-1"},
            ],
        },
    }


def _natural_slash_payload() -> dict[str, Any]:
    payload = _meeting_payload()
    payload["data"]["options"][0]["value"] = (
        "버류얼 유튜버 2d기반이걸 3d로 만들어 볼려고해. 추천작업 알려줘"
    )
    return payload


def test_runtime_smoke_packet_drives_runtime_boundaries(tmp_path: Path):
    posted: list[tuple[str, str, str]] = []
    qwen_calls: list[dict[str, Any]] = []
    glm_calls: list[dict[str, Any]] = []
    openclaw_calls: list[dict[str, Any]] = []

    def post_thread(thread_id: str, content: str) -> dict[str, str]:
        posted.append(("thread", thread_id, content))
        return {"message_id": f"msg-{thread_id}"}

    def cross_post(channel_id: str, content: str) -> dict[str, str]:
        posted.append(("channel", channel_id, content))
        return {"message_id": f"msg-{channel_id}"}

    def qwen_runner(command, timeout_seconds, env, workdir):
        qwen_calls.append(
            {"command": command, "timeout": timeout_seconds, "workdir": workdir}
        )
        return (0, json.dumps({"agenda_type": "technical", "confidence": 0.91}), "")

    def glm_runner(command, timeout_seconds, env, workdir):
        glm_calls.append(
            {"command": command, "timeout": timeout_seconds, "workdir": workdir}
        )
        return (0, json.dumps({"verdict": "pass", "confidence": 0.88}), "")

    def openclaw_executor(action):
        openclaw_calls.append(action)
        return {"execution_id": action["execution_id"], "state": "completed"}

    result = run_runtime_smoke_packet(
        payload=_meeting_payload(),
        config=RuntimeSmokeConfig(
            meetings_root=str(tmp_path / "meetings"),
            workdir=str(tmp_path),
            qwen_model="qwen-max",
            glm_model="glm-5.1",
            openclaw_expected_duration_seconds=5,
        ),
        dependencies=RuntimeSmokeDependencies(
            post_thread=post_thread,
            cross_post=cross_post,
            qwen_runner=qwen_runner,
            glm_runner=glm_runner,
            openclaw_executor=openclaw_executor,
        ),
    )

    assert result.success
    assert result.meeting_id
    assert result.discord_thread_message_id == "msg-thread-1"
    assert result.discord_cross_post_message_id == "msg-results-1"
    assert [kind for kind, _, _ in posted] == ["thread", "channel"]
    assert qwen_calls and glm_calls
    assert "--context-file" in qwen_calls[0]["command"]
    assert "--prompt" not in qwen_calls[0]["command"]
    assert qwen_calls[0]["command"][0] == "opencode-go"
    assert glm_calls[0]["command"][0] == "opencode-go"
    assert result.context_packet_path.endswith("runtime_smoke_packet.json")
    packet = json.loads(Path(result.context_packet_path).read_text(encoding="utf-8"))
    assert packet["meeting_id"] == result.meeting_id
    assert packet["discord"]["thread_id"] == "thread-1"
    assert result.qwen_success
    assert result.glm_success
    assert result.openclaw_state == "completed"
    assert openclaw_calls[0]["mode"] == "synchronous"


def test_runtime_smoke_packet_treats_slash_topic_as_natural_meeting_request(tmp_path: Path):
    posted: list[tuple[str, str]] = []

    def post(channel_id: str, content: str) -> dict[str, str]:
        posted.append((channel_id, content))
        return {"message_id": f"msg-{len(posted)}"}

    def ok_runner(command, timeout_seconds, env, workdir):
        return (0, "{}", "")

    result = run_runtime_smoke_packet(
        payload=_natural_slash_payload(),
        config=RuntimeSmokeConfig(
            meetings_root=str(tmp_path / "meetings"),
            workdir=str(tmp_path),
            openclaw_risk_level="high",
        ),
        dependencies=RuntimeSmokeDependencies(
            post_thread=post,
            cross_post=post,
            qwen_runner=ok_runner,
            glm_runner=ok_runner,
            openclaw_executor=lambda action: {"state": "completed"},
        ),
    )

    assert result.success
    assert result.stage == "complete"
    assert any("버류얼 유튜버" in content for _, content in posted)


def test_runtime_smoke_packet_blocks_unapproved_high_risk_action(tmp_path: Path):
    openclaw_calls: list[dict[str, Any]] = []

    result = run_runtime_smoke_packet(
        payload=_meeting_payload(),
        config=RuntimeSmokeConfig(
            meetings_root=str(tmp_path / "meetings"),
            workdir=str(tmp_path),
            openclaw_action_type="file_write",
            openclaw_risk_level="high",
            openclaw_approval_token="",
        ),
        dependencies=RuntimeSmokeDependencies(
            post_thread=lambda thread_id, content: {"message_id": "thread-msg"},
            cross_post=lambda channel_id, content: {"message_id": "cross-msg"},
            qwen_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            glm_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            openclaw_executor=lambda action: openclaw_calls.append(action)
            or {"state": "completed"},
        ),
    )

    assert result.success
    assert result.openclaw_state == "blocked_for_approval"
    assert openclaw_calls == []
    assert "approval" in result.openclaw_error.lower()


def test_runtime_smoke_packet_high_risk_default_fails_closed(tmp_path: Path):
    openclaw_calls: list[dict[str, Any]] = []

    result = run_runtime_smoke_packet(
        payload=_meeting_payload(),
        config=RuntimeSmokeConfig(
            meetings_root=str(tmp_path / "meetings"),
            workdir=str(tmp_path),
            openclaw_action_type="file_write",
            openclaw_risk_level="high",
        ),
        dependencies=RuntimeSmokeDependencies(
            post_thread=lambda thread_id, content: {"message_id": "thread-msg"},
            cross_post=lambda channel_id, content: {"message_id": "cross-msg"},
            qwen_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            glm_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            openclaw_executor=lambda action: openclaw_calls.append(action)
            or {"state": "completed"},
        ),
    )

    assert result.success
    assert result.openclaw_state == "blocked_for_approval"
    assert openclaw_calls == []


def test_runtime_smoke_packet_returns_structured_worker_failure(tmp_path: Path):
    def raising_qwen(command, timeout_seconds, env, workdir):
        raise RuntimeError("qwen runner crashed")

    result = run_runtime_smoke_packet(
        payload=_meeting_payload(),
        config=RuntimeSmokeConfig(
            meetings_root=str(tmp_path / "meetings"),
            workdir=str(tmp_path),
        ),
        dependencies=RuntimeSmokeDependencies(
            post_thread=lambda thread_id, content: {"message_id": "thread-msg"},
            cross_post=lambda channel_id, content: {"message_id": "cross-msg"},
            qwen_runner=raising_qwen,
            glm_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            openclaw_executor=lambda action: {"state": "completed"},
        ),
    )

    assert not result.success
    assert result.stage == "worker_validation"
    assert "qwen runner crashed" in result.error


def test_runtime_smoke_packet_surfaces_discord_delivery_failure(tmp_path: Path):
    def failing_post(thread_id: str, content: str) -> dict[str, str]:
        raise RuntimeError("Discord API unavailable")

    result = run_runtime_smoke_packet(
        payload=_meeting_payload(),
        config=RuntimeSmokeConfig(
            meetings_root=str(tmp_path / "meetings"),
            workdir=str(tmp_path),
        ),
        dependencies=RuntimeSmokeDependencies(
            post_thread=failing_post,
            cross_post=lambda channel_id, content: {"message_id": "cross-msg"},
            qwen_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            glm_runner=lambda command, timeout_seconds, env, workdir: (0, "{}", ""),
            openclaw_executor=lambda action: {"state": "completed"},
        ),
    )

    assert not result.success
    assert result.stage == "discord_delivery"
    assert "Discord API unavailable" in result.error
