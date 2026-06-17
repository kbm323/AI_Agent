from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.runtime_smoke_cli import build_default_payload, main


def test_build_default_payload_uses_required_discord_fields():
    payload = build_default_payload(
        guild_id="guild-1",
        channel_id="channel-1",
        thread_id="thread-1",
        user_id="user-1",
        topic="라이브 스모크 회의",
        result_channel_id="results-1",
    )

    assert payload["guild_id"] == "guild-1"
    assert payload["channel_id"] == "channel-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["member"]["user"]["id"] == "user-1"
    assert payload["data"]["options"] == [
        {"name": "topic", "value": "라이브 스모크 회의"},
        {"name": "result_channel_id", "value": "results-1"},
    ]


def test_cli_dry_run_prints_missing_prerequisites_without_live_calls(
    tmp_path: Path,
    capsys,
):
    exit_code = main(
        [
            "--dry-run",
            "--meetings-root",
            str(tmp_path / "meetings"),
            "--workdir",
            str(tmp_path),
            "--topic",
            "라이브 스모크 회의",
            "--channel-id",
            "channel-1",
            "--thread-id",
            "thread-1",
            "--result-channel-id",
            "results-1",
            "--guild-id",
            "guild-1",
            "--user-id",
            "user-1",
        ],
        env={},
        executable_resolver=lambda name: None,
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["mode"] == "dry-run"
    assert "DISCORD_TOKEN or DISCORD_BOT_TOKEN" in report["missing"]
    assert "opencode-go" in report["missing"]


def test_cli_runs_injected_smoke_packet(tmp_path: Path, capsys):
    calls: list[dict[str, Any]] = []

    def runner(*, payload, config, dependencies):
        calls.append(
            {
                "payload": payload,
                "meetings_root": config.meetings_root,
                "qwen_model": config.qwen_model,
                "glm_model": config.glm_model,
                "openclaw_risk_level": config.openclaw_risk_level,
                "deps": dependencies,
            }
        )

        class Result:
            success = True
            stage = "complete"
            error = ""
            meeting_id = "meeting-1"
            context_packet_path = str(tmp_path / "packet.json")
            discord_thread_message_id = "thread-msg"
            discord_cross_post_message_id = "cross-msg"
            qwen_success = True
            glm_success = True
            openclaw_state = "completed"
            openclaw_error = ""

        return Result()

    exit_code = main(
        [
            "--meetings-root",
            str(tmp_path / "meetings"),
            "--workdir",
            str(tmp_path),
            "--topic",
            "실제 어댑터 스모크",
            "--channel-id",
            "channel-1",
            "--thread-id",
            "thread-1",
            "--result-channel-id",
            "results-1",
            "--guild-id",
            "guild-1",
            "--user-id",
            "user-1",
        ],
        env={"DISCORD_TOKEN": "secret"},
        smoke_runner=runner,
        dependency_factory=lambda config: "deps",
        executable_resolver=lambda name: "/bin/" + name,
    )

    assert exit_code == 0
    assert calls[0]["payload"]["data"]["options"][0]["value"] == "실제 어댑터 스모크"
    assert calls[0]["qwen_model"] == "qwen3.6-plus"
    assert calls[0]["glm_model"] == "glm-5.1"
    assert calls[0]["openclaw_risk_level"] == "high"
    assert calls[0]["deps"] == "deps"
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["stage"] == "complete"
    assert report["meeting_id"] == "meeting-1"
