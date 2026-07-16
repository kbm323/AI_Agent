from __future__ import annotations

import asyncio
import builtins
import importlib.util
import inspect
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PROJECT_ROOT / "hermes_plugins" / "ai-agent-commands"
TOOL_NAME = "save_discord_thread_to_obsidian"
SAVE_FAILED = "대화를 저장하지 못했습니다. 잠시 후 /save를 다시 시도해주세요."
SAVE_IN_PROGRESS = "대화를 저장하고 있습니다."


class FakePluginContext:
    def __init__(self) -> None:
        self.commands: dict[str, dict[str, object]] = {}
        self.tools: dict[str, dict[str, object]] = {}
        self.hooks: dict[str, object] = {}
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

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks[name] = callback


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


def _stub_successful_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    side_effect: object | None = None,
) -> AsyncMock:
    from src.runtime_architecture_v2 import (
        discord_conversation,
        hermes_command_context,
        save_command,
    )
    from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext

    monkeypatch.setenv("AI_AGENT_ROOT", str(PROJECT_ROOT))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "profile-token")
    monkeypatch.setattr(
        discord_conversation, "load_bot_identities", Mock(return_value={})
    )
    monkeypatch.setattr(
        hermes_command_context,
        "read_hermes_command_context",
        Mock(
            return_value=HermesCommandContext(
                platform="discord",
                thread_id="200",
                session_id="session-plugin",
                invocation_message_id="250",
            )
        ),
    )
    run_save = (
        AsyncMock(return_value=object())
        if side_effect is None
        else AsyncMock(side_effect=side_effect)
    )
    monkeypatch.setattr(save_command, "run_save_command", run_save)
    monkeypatch.setattr(
        save_command,
        "render_save_response",
        Mock(return_value="rendered response"),
    )
    return run_save


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


def test_plugin_registers_save_command_and_async_parameterless_tool() -> None:
    ctx, tool = _registered_tool()

    assert list(ctx.commands) == ["save"]
    command = ctx.commands["save"]
    assert command["description"] == "Save the current Discord thread to Obsidian."
    assert command["args_hint"] == ""
    assert inspect.iscoroutinefunction(command["handler"])
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
    assert list(ctx.hooks) == ["pre_gateway_dispatch"]


@pytest.mark.asyncio
async def test_save_command_runs_existing_save_pipeline_and_returns_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    run_save = _stub_successful_save(monkeypatch, tmp_path)

    response = await ctx.commands["save"]["handler"]("")

    assert response == "rendered response"
    run_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_command_rejects_trailing_arguments_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    env_get = Mock(side_effect=AssertionError("configuration must not be read"))
    monkeypatch.setattr(plugin.os.environ, "get", env_get)

    response = await ctx.commands["save"]["handler"]("unexpected")

    assert response == "\uc0ac\uc6a9\ubc95: /save"
    env_get.assert_not_called()


@pytest.mark.asyncio
async def test_pre_dispatch_hook_floors_exact_slash_interaction_cutoff_to_timestamp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    run_save = _stub_successful_save(monkeypatch, tmp_path)
    hook = ctx.hooks["pre_gateway_dispatch"]
    source = SimpleNamespace(
        platform=SimpleNamespace(value="discord"),
        chat_id="200",
        thread_id="200",
        chat_type="thread",
    )
    timestamp_floor = (123456789012345678 >> 22) << 22
    raw_interaction_id = timestamp_floor + ((1 << 22) - 1)
    event = SimpleNamespace(
        source=source,
        raw_message=SimpleNamespace(id=raw_interaction_id),
        message_id=None,
    )

    assert callable(hook)
    assert hook(event=event, gateway=object(), session_store=object()) is None
    handler = ctx.tools[TOOL_NAME]["handler"]
    await handler({}, session_id="session-hook", task_id="turn-hook")

    command_context = run_save.await_args.kwargs["context"]
    assert command_context.invocation_message_id == str(timestamp_floor)
    assert command_context.invocation_boundary_kind == "discord_interaction_id"
    assert command_context.source_kind == "thread"


@pytest.mark.asyncio
async def test_pre_dispatch_fallback_cutoff_is_frozen_before_tool_delay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin = _load_plugin()
    monkeypatch.setattr(plugin.time, "time", Mock(return_value=1_800_000_000.0))
    ctx = FakePluginContext()
    plugin.register(ctx)
    run_save = _stub_successful_save(monkeypatch, tmp_path)
    hook = ctx.hooks["pre_gateway_dispatch"]
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="discord",
            chat_id="200",
            thread_id="200",
            chat_type="thread",
        ),
        raw_message=SimpleNamespace(id=None),
        message_id=None,
    )

    hook(event=event, gateway=object(), session_store=object())
    monkeypatch.setattr(plugin.time, "time", Mock(return_value=1_900_000_000.0))
    handler = ctx.tools[TOOL_NAME]["handler"]
    await handler({}, session_id="session-fallback", task_id="turn-fallback")

    command_context = run_save.await_args.kwargs["context"]
    expected = str((1_800_000_000_000 - 1_420_070_400_000) << 22)
    assert command_context.invocation_message_id == expected
    assert command_context.invocation_boundary_kind == "gateway_turn_start"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [None, [], "", {"unexpected": True}])
async def test_save_tool_rejects_non_dict_or_non_empty_args_before_setup(
    monkeypatch: pytest.MonkeyPatch,
    args: object,
) -> None:
    plugin = _load_plugin()
    env_get = Mock(side_effect=AssertionError("configuration must not be read"))
    monkeypatch.setattr(plugin.os.environ, "get", env_get)
    ctx = FakePluginContext()
    plugin.register(ctx)
    handler = ctx.tools[TOOL_NAME]["handler"]
    assert callable(handler)

    response = await handler(
        args,
        session_id="session-strict-args",
        task_id="turn-strict-args",
    )

    assert _message(response) == SAVE_FAILED
    env_get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dispatch_context",
    [
        {},
        {"session_id": "session", "task_id": ""},
        {"session_id": " ", "task_id": "turn"},
        {"session_id": "session", "task_id": None},
    ],
)
async def test_save_tool_requires_nonblank_session_and_task_identity_before_setup(
    monkeypatch: pytest.MonkeyPatch,
    dispatch_context: dict[str, object],
) -> None:
    plugin = _load_plugin()
    env_get = Mock(side_effect=AssertionError("configuration must not be read"))
    monkeypatch.setattr(plugin.os.environ, "get", env_get)
    ctx = FakePluginContext()
    plugin.register(ctx)
    handler = ctx.tools[TOOL_NAME]["handler"]
    assert callable(handler)

    response = await handler({}, **dispatch_context)

    assert _message(response) == SAVE_FAILED
    env_get.assert_not_called()


@pytest.mark.asyncio
async def test_save_tool_sequential_duplicate_returns_cached_result_without_resaving(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, tool = _registered_tool()
    run_save = _stub_successful_save(monkeypatch, tmp_path)
    handler = tool["handler"]
    assert callable(handler)
    dispatch_context = {"session_id": "session-1", "task_id": "turn-1"}

    first = await handler({}, **dispatch_context)
    duplicate = await handler({}, **dispatch_context)

    assert duplicate == first
    assert _message(first) == "rendered response"
    run_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_tool_concurrent_duplicate_runs_save_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    save_calls = 0

    async def delayed_save(**_kwargs: object) -> object:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            started.set()
            await release.wait()
        return object()

    _, tool = _registered_tool()
    run_save = _stub_successful_save(
        monkeypatch,
        tmp_path,
        side_effect=delayed_save,
    )
    handler = tool["handler"]
    assert callable(handler)
    dispatch_context = {"session_id": "session-2", "task_id": "turn-2"}

    owner = asyncio.create_task(handler({}, **dispatch_context))
    await started.wait()
    concurrent = await handler({}, **dispatch_context)
    release.set()
    first = await owner
    cached = await handler({}, **dispatch_context)

    assert _message(concurrent) == SAVE_IN_PROGRESS
    assert cached == first
    run_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_tool_allows_future_turn_with_different_task_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, tool = _registered_tool()
    run_save = _stub_successful_save(monkeypatch, tmp_path)
    handler = tool["handler"]
    assert callable(handler)

    await handler({}, session_id="session-3", task_id="turn-3a")
    await handler({}, session_id="session-3", task_id="turn-3b")

    assert run_save.await_count == 2


@pytest.mark.asyncio
async def test_bounded_registry_evicts_completed_entry_without_breaking_active_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    save_calls = 0

    async def first_save_waits(**_kwargs: object) -> object:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            started.set()
            await release.wait()
        return object()

    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_MAX_INVOCATIONS", 2)
    ctx = FakePluginContext()
    plugin.register(ctx)
    run_save = _stub_successful_save(
        monkeypatch,
        tmp_path,
        side_effect=first_save_waits,
    )
    handler = ctx.tools[TOOL_NAME]["handler"]
    assert callable(handler)

    active = asyncio.create_task(
        handler({}, session_id="session-4", task_id="turn-active")
    )
    await started.wait()
    await handler({}, session_id="session-4", task_id="turn-completed")
    await handler({}, session_id="session-4", task_id="turn-evicts-completed")
    active_duplicate = await handler({}, session_id="session-4", task_id="turn-active")

    assert _message(active_duplicate) == SAVE_IN_PROGRESS
    assert len(plugin._invocations) == 2
    assert (
        ctx.profile_name,
        "session-4",
        "turn-active",
    ) in plugin._invocations

    release.set()
    first = await active
    cached = await handler({}, session_id="session-4", task_id="turn-active")

    assert cached == first
    assert run_save.await_count == 3


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
        session_id="late-bound-session",
        invocation_message_id="250",
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
    response = await handler(
        {},
        session_id="late-bound-session",
        task_id="late-bound-turn",
    )

    assert _message(response) == "rendered response"
    read_context.assert_called_once_with()
    history_factory.assert_called_once_with(
        token="profile-token",
        checkpoint_root=PROJECT_ROOT / "runtime" / "discord_save" / "collection",
    )
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
            session_id="late-bound-session",
            invocation_message_id="250",
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
        (
            "DISCORD_BOT_TOKEN",
            "Discord 봇 토큰이 설정되지 않았습니다. 토큰을 설정한 뒤 "
            "/save를 다시 실행해주세요.",
        ),
        (
            "OBSIDIAN_VAULT_PATH",
            "Obsidian 보관함을 사용할 수 없습니다. 보관함 경로와 쓰기 권한을 "
            "확인한 뒤 /save를 다시 시도해주세요.",
        ),
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
    response = await handler(
        {},
        session_id="configuration-session",
        task_id=f"configuration-{missing_name}",
    )

    assert _message(response) == expected_message
    assert list(ctx.commands) == ["save"]


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
    response = await handler(
        {},
        session_id="missing-root-session",
        task_id="missing-root-turn",
    )

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
    response = await handler(
        {},
        session_id="import-session",
        task_id="import-turn",
    )

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
    response = await handler(
        {},
        session_id="identity-session",
        task_id="identity-turn",
    )

    assert _message(response) == SAVE_FAILED
    assert expected_exception_text not in response
    run_save.assert_not_awaited()
