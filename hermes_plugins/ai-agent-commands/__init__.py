# ruff: noqa: N999

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

_DEFAULT_AI_AGENT_ROOT = "/home/ubuntu/hermes-workspace/AI_Agent"
_USAGE = "사용법: /save"


def register(ctx: Any) -> None:
    async def handle_save(raw_args: str) -> str:
        if raw_args.strip():
            return _USAGE

        root = Path(os.environ.get("AI_AGENT_ROOT", _DEFAULT_AI_AGENT_ROOT))
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
        from src.runtime_architecture_v2.discord_history import DiscordHistoryClient
        from src.runtime_architecture_v2.hermes_command_context import (
            read_hermes_command_context,
        )
        from src.runtime_architecture_v2.obsidian_conversations import (
            ObsidianConversationStore,
        )
        from src.runtime_architecture_v2.save_command import (
            SaveCommandResult,
            render_save_response,
            run_save_command,
        )
        from src.runtime_architecture_v2.store import MeetingRunStore

        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            return render_save_response(
                SaveCommandResult(ok=False, error="missing_discord_token")
            )

        vault_path = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
        if not vault_path:
            return render_save_response(
                SaveCommandResult(ok=False, error="vault_unavailable")
            )

        try:
            identities = load_bot_identities(
                root / "runtime" / "discord_bot_identities.json"
            )
        except (OSError, TypeError, ValueError):
            return render_save_response(
                SaveCommandResult(ok=False, error="save_failed")
            )

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
        return render_save_response(result)

    ctx.register_command(
        "save",
        handler=handle_save,
        description="현재 Discord 스레드를 Obsidian에 저장합니다.",
        args_hint="",
    )
