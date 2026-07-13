# ruff: noqa: N999

from __future__ import annotations

import json
import os
import sys
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
_MISSING_TOKEN_RESPONSE = "Discord 봇 토큰이 설정되지 않았습니다."
_VAULT_UNAVAILABLE_RESPONSE = "Obsidian 보관함을 사용할 수 없습니다."
_SAVE_FAILED_RESPONSE = "대화를 저장하지 못했습니다."


def _tool_result(message: str) -> str:
    return json.dumps({"message": message}, ensure_ascii=False)


def register(ctx: Any) -> None:
    async def handle_save(_args: dict[str, Any], **_kwargs: Any) -> str:
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
            if not context.profile:
                context = replace(context, profile=ctx.profile_name)
            result = await run_save_command(
                context=context,
                history_client=DiscordHistoryClient(token=token),
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

    ctx.register_tool(
        name=_TOOL_NAME,
        toolset=_TOOLSET,
        schema=_TOOL_SCHEMA,
        handler=handle_save,
        is_async=True,
        description=_TOOL_DESCRIPTION,
    )
