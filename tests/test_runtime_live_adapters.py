from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.opencode_glm_wrapper import inject_runner as inject_glm_runner
from src.opencode_qwen_wrapper import inject_runner as inject_qwen_runner
from src.runtime_live_adapters import (
    DiscordLiveConfig,
    OpenClawLiveConfig,
    RuntimeLiveAdapterConfig,
    create_discord_cross_poster,
    create_discord_thread_poster,
    create_openclaw_executor,
    create_runtime_smoke_live_dependencies,
    load_runtime_live_config,
    load_runtime_live_env,
)


def test_discord_thread_poster_sends_bot_message_to_channel_endpoint():
    calls: list[dict[str, Any]] = []

    def http_post(url: str, *, headers: dict[str, str], body: bytes, timeout: float):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "body": json.loads(body.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return 200, {"id": "msg-123", "channel_id": "thread-1"}

    poster = create_discord_thread_poster(
        DiscordLiveConfig(bot_token="bot-token", api_base="https://discord.test/api/v10"),
        http_post=http_post,
    )

    receipt = poster("thread-1", "회의 결과")

    assert receipt == {"message_id": "msg-123", "channel_id": "thread-1"}
    assert calls == [
        {
            "url": "https://discord.test/api/v10/channels/thread-1/messages",
            "headers": {
                "Authorization": "Bot bot-token",
                "Content-Type": "application/json",
                "User-Agent": "AI_Agent runtime smoke/1.0",
            },
            "body": {"content": "회의 결과"},
            "timeout": 10.0,
        }
    ]


def test_discord_cross_poster_rejects_failed_discord_response():
    def http_post(url: str, *, headers: dict[str, str], body: bytes, timeout: float):
        return 403, {"message": "Missing Permissions"}

    poster = create_discord_cross_poster(
        DiscordLiveConfig(bot_token="bot-token"),
        http_post=http_post,
    )

    with pytest.raises(RuntimeError, match="Discord message post failed: 403"):
        poster("results-1", "요약")


def test_openclaw_executor_writes_packet_file_and_invokes_command(
    tmp_path: Path,
):
    calls: list[dict[str, Any]] = []

    def runner(command, timeout_seconds, env, workdir):
        calls.append(
            {
                "command": command,
                "timeout": timeout_seconds,
                "env": env,
                "workdir": workdir,
            }
        )
        packet_path = Path(command[-1])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        assert packet["action_type"] == "diagnostic_read"
        assert packet["risk_level"] == "low"
        return 0, json.dumps({"state": "completed", "execution_id": "exec-1"}), ""

    executor = create_openclaw_executor(
        OpenClawLiveConfig(
            command=("openclaw", "execute", "--packet"),
            packet_dir=str(tmp_path / "packets"),
            workdir=str(tmp_path),
            timeout_seconds=30.0,
            env={"OPENCLAW_PROFILE": "test"},
        ),
        runner=runner,
    )

    receipt = executor(
        {
            "execution_id": "exec-1",
            "action_type": "diagnostic_read",
            "risk_level": "low",
            "target": "runtime-smoke",
            "mode": "synchronous",
            "meeting_id": "meeting-1",
        }
    )

    assert receipt["state"] == "completed"
    assert calls == [
        {
            "command": [
                "openclaw",
                "execute",
                "--packet",
                str(tmp_path / "packets" / "exec-1.json"),
            ],
            "timeout": 30.0,
            "env": {"OPENCLAW_PROFILE": "test"},
            "workdir": str(tmp_path),
        }
    ]


def test_openclaw_executor_reports_nonzero_exit(tmp_path: Path):
    def runner(command, timeout_seconds, env, workdir):
        return 2, "", "boom"

    executor = create_openclaw_executor(
        OpenClawLiveConfig(command=("openclaw",), packet_dir=str(tmp_path)),
        runner=runner,
    )

    with pytest.raises(RuntimeError, match="OpenClaw command failed with code 2: boom"):
        executor({"execution_id": "exec-2", "action_type": "diagnostic_read"})


def test_load_runtime_live_config_reads_env(
    tmp_path: Path,
):
    env = {
        "DISCORD_TOKEN": "discord-secret",
        "DISCORD_API_BASE": "https://discord.test/api/v10",
        "OPENCLAW_COMMAND": "openclaw execute --packet",
        "OPENCLAW_PACKET_DIR": str(tmp_path / "openclaw"),
        "AI_AGENT_WORKDIR": str(tmp_path),
        "QWEN_MODEL": "qwen3.6-plus",
        "GLM_MODEL": "glm-5.1",
    }

    config = load_runtime_live_config(env)

    assert config.discord.bot_token == "discord-secret"
    assert config.discord.api_base == "https://discord.test/api/v10"
    assert config.openclaw.command == ("openclaw", "execute", "--packet")
    assert config.openclaw.packet_dir == str(tmp_path / "openclaw")
    assert config.workdir == str(tmp_path)
    assert config.qwen_model == "qwen3.6-plus"
    assert config.glm_model == "glm-5.1"


def test_load_runtime_live_env_reads_hermes_env_when_no_env_is_injected(
    tmp_path: Path,
    monkeypatch,
):
    hermes_env = tmp_path / ".env"
    hermes_env.write_text(
        "DISCORD_BOT_TOKEN=discord-from-hermes\n"
        "OPENCODE_GO_API_KEY=opencode-from-hermes\n"
        "NOTION_API_KEY=notion-from-hermes\n"
        "NOTION_SECOND_BRAIN_ROOT_PAGE_ID=second-brain-root\n"
        "GLM_MODEL=glm-5.1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_SECOND_BRAIN_ROOT_PAGE_ID", raising=False)
    monkeypatch.delenv("NOTION_SCHEDULE_DATA_SOURCE_ID", raising=False)
    monkeypatch.delenv("NOTION_IDEA_DATA_SOURCE_ID", raising=False)
    monkeypatch.setenv("QWEN_MODEL", "qwen-from-process")

    env = load_runtime_live_env(None, hermes_env_path=hermes_env)
    config = load_runtime_live_config(None, hermes_env_path=hermes_env)

    assert env["DISCORD_BOT_TOKEN"] == "discord-from-hermes"
    assert env["OPENCODE_GO_API_KEY"] == "opencode-from-hermes"
    assert env["QWEN_MODEL"] == "qwen-from-process"
    assert config.discord.bot_token == "discord-from-hermes"
    assert config.subprocess_env == {
        "OPENCODE_GO_API_KEY": "opencode-from-hermes",
        "OPENCODE_API_KEY": "opencode-from-hermes",
        "NOTION_API_KEY": "notion-from-hermes",
        "NOTION_API_TOKEN": "notion-from-hermes",
        "NOTION_SECOND_BRAIN_ROOT_PAGE_ID": "second-brain-root",
    }


def test_load_runtime_live_config_aliases_notion_api_token():
    config = load_runtime_live_config(
        {
            "DISCORD_BOT_TOKEN": "discord-secret",
            "NOTION_API_TOKEN": "notion-token-alias",
            "NOTION_SCHEDULE_DATA_SOURCE_ID": "schedule-ds",
            "NOTION_IDEA_DATA_SOURCE_ID": "idea-ds",
        }
    )

    assert config.subprocess_env["NOTION_API_KEY"] == "notion-token-alias"
    assert config.subprocess_env["NOTION_API_TOKEN"] == "notion-token-alias"
    assert config.subprocess_env["NOTION_SCHEDULE_DATA_SOURCE_ID"] == "schedule-ds"
    assert config.subprocess_env["NOTION_IDEA_DATA_SOURCE_ID"] == "idea-ds"


def test_create_runtime_smoke_live_dependencies_wires_real_boundaries(tmp_path: Path):
    config = RuntimeLiveAdapterConfig(
        discord=DiscordLiveConfig(bot_token="bot-token"),
        openclaw=OpenClawLiveConfig(command=("openclaw",), packet_dir=str(tmp_path)),
        workdir=str(tmp_path),
    )

    deps = create_runtime_smoke_live_dependencies(
        config,
        http_post=lambda url, *, headers, body, timeout: (200, {"id": "msg"}),
        openclaw_runner=lambda command, timeout_seconds, env, workdir: (
            0,
            '{"state":"completed"}',
            "",
        ),
    )

    assert deps.post_thread("thread-1", "hello")["message_id"] == "msg"
    assert deps.cross_post("results-1", "summary")["message_id"] == "msg"
    assert deps.openclaw_executor({"execution_id": "exec-3"})["state"] == "completed"
    assert callable(deps.qwen_runner)
    assert callable(deps.glm_runner)


def test_worker_runners_receive_loaded_subprocess_env(tmp_path: Path):
    calls: list[dict[str, str] | None] = []

    def runner(command, timeout_seconds, env, workdir):
        calls.append(env)
        return 0, "ok", ""

    inject_qwen_runner(runner)
    inject_glm_runner(runner)
    try:
        config = RuntimeLiveAdapterConfig(
            discord=DiscordLiveConfig(bot_token="bot-token"),
            openclaw=OpenClawLiveConfig(
                command=("openclaw",),
                packet_dir=str(tmp_path),
            ),
            subprocess_env={
                "OPENCODE_GO_API_KEY": "opencode-secret",
                "OPENCODE_API_KEY": "opencode-secret",
            },
        )
        deps = create_runtime_smoke_live_dependencies(
            config,
            http_post=lambda url, *, headers, body, timeout: (200, {"id": "msg"}),
            openclaw_runner=lambda command, timeout_seconds, env, workdir: (
                0,
                "{}",
                "",
            ),
        )

        deps.qwen_runner(["opencode-go"], 1.0, {"EXTRA": "1"}, None)
        deps.glm_runner(["opencode-go"], 1.0, None, None)
    finally:
        inject_qwen_runner(None)
        inject_glm_runner(None)

    assert calls == [
        {
            "OPENCODE_GO_API_KEY": "opencode-secret",
            "OPENCODE_API_KEY": "opencode-secret",
            "EXTRA": "1",
        },
        {
            "OPENCODE_GO_API_KEY": "opencode-secret",
            "OPENCODE_API_KEY": "opencode-secret",
        },
    ]
