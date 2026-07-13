from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1] / "hermes_plugins" / "ai-agent-commands"
)


class FakePluginContext:
    def __init__(self) -> None:
        self.commands: dict[str, dict[str, object]] = {}
        self.llm = object()
        self.profile_name = "aicompanyassistant"

    def register_command(
        self,
        name: str,
        *,
        handler: object,
        description: str,
        args_hint: str,
    ) -> None:
        self.commands[name] = {
            "handler": handler,
            "description": description,
            "args_hint": args_hint,
        }


def _load_plugin() -> ModuleType:
    path = PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location("ai_agent_commands_plugin", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_manifest_declares_no_secret_or_provider_dependencies() -> None:
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")

    assert manifest == (
        "name: ai-agent-commands\n"
        "version: 0.1.0\n"
        'description: "Runtime Architecture v2 Discord commands for meetings '
        'and Obsidian knowledge capture."\n'
        'author: "kbm323"\n'
    )
    assert "requires_env" not in manifest
    assert "provider" not in manifest.lower()


def test_plugin_registers_parameterless_save_command() -> None:
    plugin = _load_plugin()
    ctx = FakePluginContext()

    plugin.register(ctx)

    assert list(ctx.commands) == ["save"]
    meta = ctx.commands["save"]
    assert meta["args_hint"] == ""
    assert meta["description"] == "현재 Discord 스레드를 Obsidian에 저장합니다."


@pytest.mark.asyncio
async def test_save_handler_rejects_trailing_arguments() -> None:
    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)

    handler = ctx.commands["save"]["handler"]
    assert callable(handler)

    result = await handler("meeting")

    assert result == "사용법: /save"


@pytest.mark.asyncio
async def test_save_handler_constructs_reviewed_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.runtime_architecture_v2 import (
        conversation_summary,
        discord_conversation,
        discord_history,
        hermes_command_context,
        obsidian_conversations,
        save_command,
        store,
    )

    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    root = tmp_path / "AI_Agent"
    vault = tmp_path / "Obsidian"
    monkeypatch.setenv("AI_AGENT_ROOT", str(root))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")

    command_context = HermesCommandContext(
        platform="discord",
        chat_id="200",
        thread_id="200",
    )
    read_context = Mock(return_value=command_context)
    history_client = object()
    meeting_store = object()
    identities = {"400": object()}
    participant_resolver = object()
    summarizer = object()
    obsidian_store = object()
    result = object()
    history_factory = Mock(return_value=history_client)
    meeting_factory = Mock(return_value=meeting_store)
    load_identities = Mock(return_value=identities)
    resolver_factory = Mock(return_value=participant_resolver)
    summarizer_factory = Mock(return_value=summarizer)
    obsidian_factory = Mock(return_value=obsidian_store)
    run_save = AsyncMock(return_value=result)
    render_response = Mock(return_value="rendered response")

    monkeypatch.setattr(
        hermes_command_context, "read_hermes_command_context", read_context
    )
    monkeypatch.setattr(discord_history, "DiscordHistoryClient", history_factory)
    monkeypatch.setattr(store, "MeetingRunStore", meeting_factory)
    monkeypatch.setattr(discord_conversation, "load_bot_identities", load_identities)
    monkeypatch.setattr(discord_conversation, "ParticipantResolver", resolver_factory)
    monkeypatch.setattr(
        conversation_summary, "HermesConversationSummarizer", summarizer_factory
    )
    monkeypatch.setattr(
        obsidian_conversations, "ObsidianConversationStore", obsidian_factory
    )
    monkeypatch.setattr(save_command, "run_save_command", run_save)
    monkeypatch.setattr(save_command, "render_save_response", render_response)

    handler = ctx.commands["save"]["handler"]
    assert callable(handler)

    response = await handler("")

    assert response == "rendered response"
    read_context.assert_called_once_with()
    history_factory.assert_called_once_with(token="profile-token")
    meeting_factory.assert_called_once_with(root)
    load_identities.assert_called_once_with(
        root / "runtime" / "discord_bot_identities.json"
    )
    resolver_factory.assert_called_once_with(identities)
    summarizer_factory.assert_called_once_with(ctx.llm)
    obsidian_factory.assert_called_once_with(
        vault_root=vault,
        runtime_root=root,
    )
    run_save.assert_awaited_once()
    assert run_save.await_args.kwargs == {
        "context": HermesCommandContext(
            platform="discord",
            chat_id="200",
            thread_id="200",
            profile=ctx.profile_name,
        ),
        "history_client": history_client,
        "meeting_store": meeting_store,
        "participant_resolver": participant_resolver,
        "summarizer": summarizer,
        "obsidian_store": obsidian_store,
    }
    render_response.assert_called_once_with(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing_name", "expected_error"),
    [
        ("DISCORD_BOT_TOKEN", "missing_discord_token"),
        ("OBSIDIAN_VAULT_PATH", "vault_unavailable"),
    ],
)
async def test_save_handler_fails_closed_when_required_profile_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_name: str,
    expected_error: str,
) -> None:
    from src.runtime_architecture_v2 import (
        discord_conversation,
        hermes_command_context,
        save_command,
    )

    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    monkeypatch.setenv("AI_AGENT_ROOT", str(tmp_path))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")
    monkeypatch.delenv(missing_name)
    monkeypatch.setattr(
        hermes_command_context,
        "read_hermes_command_context",
        Mock(return_value=HermesCommandContext(platform="discord", thread_id="200")),
    )
    monkeypatch.setattr(
        discord_conversation, "load_bot_identities", Mock(return_value={})
    )
    run_save = AsyncMock()
    monkeypatch.setattr(save_command, "run_save_command", run_save)
    monkeypatch.setattr(
        save_command,
        "render_save_response",
        Mock(side_effect=lambda result: result.error),
    )

    handler = ctx.commands["save"]["handler"]
    assert callable(handler)

    response = await handler("")

    assert response == expected_error
    run_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_handler_fails_closed_when_identity_map_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.runtime_architecture_v2 import discord_conversation, save_command

    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    monkeypatch.setenv("AI_AGENT_ROOT", str(tmp_path))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")
    monkeypatch.setattr(
        discord_conversation,
        "load_bot_identities",
        Mock(side_effect=OSError("secret local path")),
    )
    run_save = AsyncMock()
    monkeypatch.setattr(save_command, "run_save_command", run_save)
    monkeypatch.setattr(
        save_command,
        "render_save_response",
        Mock(side_effect=lambda result: result.error),
    )

    handler = ctx.commands["save"]["handler"]
    assert callable(handler)

    response = await handler("")

    assert response == "save_failed"
    assert "secret local path" not in response
    run_save.assert_not_awaited()
