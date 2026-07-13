# ruff: noqa: N999

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any

_DEFAULT_AI_AGENT_ROOT = "/home/ubuntu/hermes-workspace/AI_Agent"
_TOOL_NAME = "save_discord_thread_to_obsidian"
_TOOLSET = "ai_agent_commands"
_TOOL_DESCRIPTION = "Save the current Discord thread to Obsidian."
_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": _TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}
_MISSING_TOKEN_RESPONSE = (
    "Discord 봇 토큰이 설정되지 않았습니다. 토큰을 설정한 뒤 /save를 다시 실행해주세요."
)
_VAULT_UNAVAILABLE_RESPONSE = (
    "Obsidian 보관함을 사용할 수 없습니다. 보관함 경로와 쓰기 권한을 확인한 뒤 "
    "/save를 다시 시도해주세요."
)
_SAVE_FAILED_RESPONSE = "대화를 저장하지 못했습니다. 잠시 후 /save를 다시 시도해주세요."
_SAVE_IN_PROGRESS_RESPONSE = "대화를 저장하고 있습니다."
_MAX_INVOCATIONS = 1024
_DISCORD_EPOCH_MS = 1_420_070_400_000
_SNOWFLAKE_RE = re.compile(r"^[0-9]{1,24}$")
_InvocationKey = tuple[str, str, str]
_invocations: OrderedDict[_InvocationKey, str | None] = OrderedDict()
_invocations_lock = threading.Lock()
_dispatch_boundary: ContextVar[dict[str, str] | None] = ContextVar(
    "ai_agent_save_dispatch_boundary",
    default=None,
)


def _tool_result(message: str) -> str:
    return json.dumps({"message": message}, ensure_ascii=False)


def _invocation_key(
    ctx: Any, dispatch_context: dict[str, Any]
) -> _InvocationKey | None:
    profile = getattr(ctx, "profile_name", None)
    session_id = dispatch_context.get("session_id")
    task_id = dispatch_context.get("task_id")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (profile, session_id, task_id)
    ):
        return None
    return profile.strip(), session_id.strip(), task_id.strip()


def _reserve_invocation(key: _InvocationKey) -> tuple[bool, str]:
    with _invocations_lock:
        if key in _invocations:
            result = _invocations[key]
            if result is None:
                return False, _tool_result(_SAVE_IN_PROGRESS_RESPONSE)
            _invocations.move_to_end(key)
            return False, result

        while len(_invocations) >= _MAX_INVOCATIONS:
            completed_key = next(
                (
                    invocation_key
                    for invocation_key, result in _invocations.items()
                    if result is not None
                ),
                None,
            )
            if completed_key is None:
                return False, _tool_result(_SAVE_FAILED_RESPONSE)
            del _invocations[completed_key]

        _invocations[key] = None
        return True, ""


def _complete_invocation(key: _InvocationKey, result: str) -> None:
    with _invocations_lock:
        if key not in _invocations:
            return
        _invocations[key] = result
        _invocations.move_to_end(key)


def _platform_name(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").casefold()


def _snowflake(value: object) -> str:
    candidate = str(value or "")
    return candidate if _SNOWFLAKE_RE.fullmatch(candidate) else ""


def _turn_start_snowflake() -> str:
    milliseconds = int(time.time() * 1000)
    return str(max(0, milliseconds - _DISCORD_EPOCH_MS) << 22)


def _capture_gateway_boundary(**kwargs: Any) -> None:
    event = kwargs.get("event")
    source = getattr(event, "source", None)
    if _platform_name(getattr(source, "platform", None)) != "discord":
        _dispatch_boundary.set(None)
        return

    raw_message = getattr(event, "raw_message", None)
    cutoff = _snowflake(getattr(raw_message, "id", None))
    boundary_kind = "discord_interaction_id"
    if not cutoff:
        cutoff = _snowflake(getattr(event, "message_id", None))
        boundary_kind = "discord_message_id"
    if not cutoff:
        cutoff = _turn_start_snowflake()
        boundary_kind = "gateway_turn_start"

    chat_type = str(getattr(source, "chat_type", "") or "").casefold()
    source_kind = (
        "dm" if chat_type == "dm" else "thread" if chat_type == "thread" else ""
    )
    _dispatch_boundary.set(
        {
            "cutoff_message_id": cutoff,
            "boundary_kind": boundary_kind,
            "source_kind": source_kind,
            "chat_id": str(getattr(source, "chat_id", "") or ""),
        }
    )


def register(ctx: Any) -> None:
    async def execute_save() -> str:
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            return _tool_result(_MISSING_TOKEN_RESPONSE)

        vault_path = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
        if not vault_path:
            return _tool_result(_VAULT_UNAVAILABLE_RESPONSE)

        try:
            root = (
                Path(os.environ.get("AI_AGENT_ROOT", _DEFAULT_AI_AGENT_ROOT))
                .expanduser()
                .resolve(strict=True)
            )
            if not (root / "src" / "runtime_architecture_v2").is_dir():
                return _tool_result(_SAVE_FAILED_RESPONSE)

            root_text = str(root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)

            from src.runtime_architecture_v2.conversation_summary import (
                HermesConversationSummarizer,
            )
            from src.runtime_architecture_v2.discord_conversation import (
                ParticipantResolver,
                load_bot_identities,
            )
            from src.runtime_architecture_v2.discord_history import (
                DiscordHistoryClient,
            )
            from src.runtime_architecture_v2.hermes_command_context import (
                read_hermes_command_context,
            )
            from src.runtime_architecture_v2.obsidian_conversations import (
                ObsidianConversationStore,
            )
            from src.runtime_architecture_v2.save_command import (
                render_save_response,
                run_save_command,
            )
            from src.runtime_architecture_v2.store import MeetingRunStore
        except (ImportError, OSError):
            return _tool_result(_SAVE_FAILED_RESPONSE)

        try:
            identities = load_bot_identities(
                root / "runtime" / "discord_bot_identities.json"
            )
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return _tool_result(_SAVE_FAILED_RESPONSE)

        try:
            context = read_hermes_command_context()
            boundary = _dispatch_boundary.get()
            if boundary is not None and (
                not context.chat_id
                or not boundary["chat_id"]
                or context.chat_id == boundary["chat_id"]
            ):
                context = replace(
                    context,
                    invocation_message_id=boundary["cutoff_message_id"],
                    invocation_boundary_kind=boundary["boundary_kind"],
                    source_kind=boundary["source_kind"],
                )
            if not context.profile:
                context = replace(context, profile=ctx.profile_name)
            result = await run_save_command(
                context=context,
                history_client=DiscordHistoryClient(
                    token=token,
                    checkpoint_root=(root / "runtime" / "discord_save" / "collection"),
                ),
                meeting_store=MeetingRunStore(root),
                participant_resolver=ParticipantResolver(identities),
                summarizer=HermesConversationSummarizer(ctx.llm),
                obsidian_store=ObsidianConversationStore(
                    vault_root=Path(vault_path),
                    runtime_root=root,
                ),
            )
            return _tool_result(render_save_response(result))
        except Exception:
            return _tool_result(_SAVE_FAILED_RESPONSE)

    async def handle_save(args: object, **dispatch_context: Any) -> str:
        if not isinstance(args, dict) or args:
            return _tool_result(_SAVE_FAILED_RESPONSE)

        key = _invocation_key(ctx, dispatch_context)
        if key is None:
            return _tool_result(_SAVE_FAILED_RESPONSE)

        should_execute, existing_result = _reserve_invocation(key)
        if not should_execute:
            return existing_result

        result = _tool_result(_SAVE_FAILED_RESPONSE)
        try:
            result = await execute_save()
        finally:
            _complete_invocation(key, result)
        return result

    ctx.register_tool(
        name=_TOOL_NAME,
        toolset=_TOOLSET,
        schema=_TOOL_SCHEMA,
        handler=handle_save,
        is_async=True,
        description=_TOOL_DESCRIPTION,
    )
    ctx.register_hook("pre_gateway_dispatch", _capture_gateway_boundary)
