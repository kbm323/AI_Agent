from __future__ import annotations

from typing import Any

from src.discord_runtime_smoke_runner import run_runtime_smoke_for_interaction


def test_run_runtime_smoke_for_interaction_builds_live_config_and_report(tmp_path):
    calls: list[dict[str, Any]] = []

    def smoke_runner(*, payload, config, dependencies):
        calls.append(
            {
                "payload": payload,
                "meetings_root": config.meetings_root,
                "workdir": config.workdir,
                "qwen_model": config.qwen_model,
                "glm_model": config.glm_model,
                "openclaw_risk_level": config.openclaw_risk_level,
                "dependencies": dependencies,
            }
        )

        class Result:
            success = True
            stage = "complete"
            error = ""
            meeting_id = "meeting-1"
            context_packet_path = "meetings/meeting-1/runtime_smoke_packet.json"
            discord_thread_message_id = "thread-msg"
            discord_cross_post_message_id = "cross-msg"
            qwen_success = True
            glm_success = True
            openclaw_state = "blocked_for_approval"
            openclaw_error = "OpenClaw approval required before execution"

        return Result()

    report = run_runtime_smoke_for_interaction(
        {
            "id": "1",
            "guild_id": "guild-1",
            "channel_id": "channel-1",
            "data": {
                "name": "meeting",
                "options": [{"name": "topic", "value": "라이브 회의해줘"}],
            },
        },
        env={
            "DISCORD_TOKEN": "bot-token",
            "AI_AGENT_MEETINGS_ROOT": str(tmp_path / "meetings"),
            "AI_AGENT_WORKDIR": str(tmp_path),
            "QWEN_MODEL": "qwen3.6-plus",
            "GLM_MODEL": "glm-5.1",
        },
        smoke_runner=smoke_runner,
        dependency_factory=lambda config: "deps",
    )

    assert report["ok"] is True
    assert report["stage"] == "complete"
    assert calls[0]["payload"]["thread_id"] == "channel-1"
    assert calls[0]["meetings_root"] == str(tmp_path / "meetings")
    assert calls[0]["workdir"] == str(tmp_path)
    assert calls[0]["qwen_model"] == "qwen3.6-plus"
    assert calls[0]["glm_model"] == "glm-5.1"
    assert calls[0]["openclaw_risk_level"] == "high"
    assert calls[0]["dependencies"] == "deps"
