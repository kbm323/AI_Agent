from __future__ import annotations

import builtins
import importlib.util
import inspect
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PROJECT_ROOT / "hermes_plugins" / "ai-agent-commands"
TOOL_NAME = "save_discord_thread_to_obsidian"
SAVE_FAILED = "대화를 저장하지 못했습니다."


class FakePluginContext:
    def __init__(self) -> None:
        self.commands: dict[str, dict[str, object]] = {}
        self.tools: dict[str, dict[str, object]] = {}
        self.llm = object()
        self.profile_name = "aicompanyassistant"

    def register_command(self, name: str, **metadata: object) -> None:
        self.commands[name] = metadata

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, object],
        handler: object,
        is_async: bool = False,
        description: str = "",
    ) -> None:
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "is_async": is_async,
            "description": description,
        }


def _load_plugin() -> ModuleType:
    path = PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location("ai_agent_commands_plugin", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registered_tool() -> tuple[FakePluginContext, dict[str, object]]:
    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    return ctx, ctx.tools[TOOL_NAME]


def _message(result: str) -> str:
    payload = json.loads(result)
    assert payload == {"message": payload["message"]}
    return payload["message"]


def test_manifest_declares_tool_without_secret_or_provider_dependencies() -> None:
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")

    assert manifest == (
        "name: ai-agent-commands\n"
        "version: 0.1.0\n"
        'description: "Runtime Architecture v2 Discord commands for meetings '
        'and Obsidian knowledge capture."\n'
        'author: "kbm323"\n'
        "provides_tools:\n"
        f"  - {TOOL_NAME}\n"
    )
    assert "requires_env" not in manifest
    assert "provider" not in manifest.lower()


def test_plugin_registers_one_async_parameterless_tool_and_no_command() -> None:
    ctx, tool = _registered_tool()

    assert ctx.commands == {}
    assert list(ctx.tools) == [TOOL_NAME]
    assert tool["toolset"] == "ai_agent_commands"
    assert tool["is_async"] is True
    assert inspect.iscoroutinefunction(tool["handler"])
    assert tool["schema"] == {
        "name": TOOL_NAME,
        "description": "Save the current Discord thread to Obsidian.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }
    assert tool["description"] == "Save the current Discord thread to Obsidian."


@pytest.mark.asyncio
async def test_save_tool_reads_late_bound_context_and_constructs_reviewed_dependencies(
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
    from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext

    ctx, tool = _registered_tool()
    vault = tmp_path / "Obsidian"
    monkeypatch.setenv("AI_AGENT_ROOT", str(PROJECT_ROOT))
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

    # The session context becomes available only after plugin registration.
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

    handler = tool["handler"]
    assert callable(handler)
    response = await handler({}, task_id="late-bound-session")

    assert _message(response) == "rendered response"
    read_context.assert_called_once_with()
    history_factory.assert_called_once_with(token="profile-token")
    meeting_factory.assert_called_once_with(PROJECT_ROOT)
    load_identities.assert_called_once_with(
        PROJECT_ROOT / "runtime" / "discord_bot_identities.json"
    )
    resolver_factory.assert_called_once_with(identities)
    summarizer_factory.assert_called_once_with(ctx.llm)
    obsidian_factory.assert_called_once_with(
        vault_root=vault,
        runtime_root=PROJECT_ROOT,
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
    ("missing_name", "expected_message"),
    [
        ("DISCORD_BOT_TOKEN", "Discord 봇 토큰이 설정되지 않았습니다."),
        ("OBSIDIAN_VAULT_PATH", "Obsidian 보관함을 사용할 수 없습니다."),
    ],
)
async def test_save_tool_fails_closed_when_required_profile_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_name: str,
    expected_message: str,
) -> None:
    ctx, tool = _registered_tool()
    monkeypatch.setenv("AI_AGENT_ROOT", str(tmp_path / "missing-root"))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")
    monkeypatch.delenv(missing_name)

    handler = tool["handler"]
    assert callable(handler)
    response = await handler({})

    assert _message(response) == expected_message
    assert ctx.commands == {}


@pytest.mark.asyncio
async def test_save_tool_sanitizes_missing_ai_agent_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "private" / "missing-ai-agent"
    _, tool = _registered_tool()
    monkeypatch.setenv("AI_AGENT_ROOT", str(missing_root))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")

    handler = tool["handler"]
    assert callable(handler)
    response = await handler({})

    assert _message(response) == SAVE_FAILED
    assert str(missing_root) not in response


@pytest.mark.asyncio
async def test_save_tool_sanitizes_runtime_import_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, tool = _registered_tool()
    monkeypatch.setenv("AI_AGENT_ROOT", str(PROJECT_ROOT))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")
    original_import = builtins.__import__

    def reject_runtime_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "src.runtime_architecture_v2.conversation_summary":
            raise ImportError("private deployment detail")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_runtime_import)
    handler = tool["handler"]
    assert callable(handler)
    response = await handler({})

    assert _message(response) == SAVE_FAILED
    assert "private deployment detail" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_content", "expected_exception_text"),
    [
        (None, "discord_bot_identities.json"),
        ("[]", "items"),
        ('{"400": {"hermes_profile": "assistant"}}', "role"),
    ],
)
async def test_save_tool_sanitizes_malformed_identity_map(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    identity_content: str | None,
    expected_exception_text: str,
) -> None:
    from src.runtime_architecture_v2 import save_command

    root = tmp_path / "AI_Agent"
    (root / "src" / "runtime_architecture_v2").mkdir(parents=True)
    if identity_content is not None:
        runtime = root / "runtime"
        runtime.mkdir()
        (runtime / "discord_bot_identities.json").write_text(
            identity_content,
            encoding="utf-8",
        )
    _, tool = _registered_tool()
    monkeypatch.setenv("AI_AGENT_ROOT", str(root))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")
    run_save = AsyncMock()
    monkeypatch.setattr(save_command, "run_save_command", run_save)

    handler = tool["handler"]
    assert callable(handler)
    response = await handler({})

    assert _message(response) == SAVE_FAILED
    assert expected_exception_text not in response
    run_save.assert_not_awaited()
