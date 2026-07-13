"""Transport-neutral orchestration for saving Discord thread conversations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .conversation_summary import HermesConversationSummarizer
from .discord_conversation import DiscordConversation, ParticipantResolver
from .discord_history import DiscordHistoryClient, DiscordHistoryError
from .hermes_command_context import HermesCommandContext
from .obsidian_conversations import ObsidianConversationStore
from .store import MeetingRunStore, StoreError

_ERROR_RESPONSES = {
    "discord_only": "/save는 Discord에서만 사용할 수 있습니다.",
    "thread_required": (
        "대화를 저장하려면 Discord 스레드 안에서 /save를 실행해주세요."
    ),
    "missing_discord_token": "Discord 봇 토큰이 설정되지 않았습니다.",
    "history_unavailable": "Discord 대화 기록을 불러오지 못했습니다.",
    "vault_unavailable": "Obsidian 보관함을 사용할 수 없습니다.",
    "save_failed": "대화를 저장하지 못했습니다.",
}
_SUMMARY_ERRORS = (OSError, RuntimeError, ValueError)


@dataclass(frozen=True)
class SaveCommandResult:
    ok: bool
    status: str = ""
    classification: str = ""
    new_message_count: int = 0
    canonical_path: str = ""
    summary: str = ""
    error: str = ""


async def run_save_command(
    *,
    context: HermesCommandContext,
    history_client: DiscordHistoryClient,
    meeting_store: MeetingRunStore,
    participant_resolver: ParticipantResolver,
    summarizer: HermesConversationSummarizer,
    obsidian_store: ObsidianConversationStore,
) -> SaveCommandResult:
    """Fetch, classify, summarize, and persist one Discord thread."""

    if context.platform != "discord":
        return SaveCommandResult(ok=False, error="discord_only")
    if not context.thread_id:
        return SaveCommandResult(ok=False, error="thread_required")

    try:
        conversation = await asyncio.to_thread(
            history_client.fetch_conversation, context.thread_id
        )
    except DiscordHistoryError as exc:
        code = exc.args[0] if len(exc.args) == 1 else ""
        if code in {"missing_discord_token", "thread_required"}:
            return SaveCommandResult(ok=False, error=code)
        return SaveCommandResult(ok=False, error="history_unavailable")

    try:
        meeting_run = await asyncio.to_thread(
            meeting_store.find_by_discord_thread_id, context.thread_id
        )
    except (OSError, StoreError):
        return SaveCommandResult(ok=False, error="save_failed")

    transcript = render_summary_input(conversation, participant_resolver)
    try:
        summary = await summarizer.summarize(transcript)
    except _SUMMARY_ERRORS:
        return SaveCommandResult(ok=False, error="save_failed")

    try:
        saved = await asyncio.to_thread(
            obsidian_store.save,
            conversation=conversation,
            participant_resolver=participant_resolver,
            summary=summary,
            meeting_run=meeting_run,
        )
    except OSError:
        return SaveCommandResult(ok=False, error="vault_unavailable")
    except (RuntimeError, ValueError):
        return SaveCommandResult(ok=False, error="save_failed")

    return SaveCommandResult(
        ok=True,
        status=saved.status,
        classification=saved.classification,
        new_message_count=saved.new_message_count,
        canonical_path=saved.canonical_path,
        summary=saved.one_line_summary,
    )


def render_summary_input(
    conversation: DiscordConversation,
    participant_resolver: ParticipantResolver,
) -> str:
    """Render a stable speaker-labelled transcript for the summarizer."""

    lines = [f"Thread: {conversation.thread_name}"]
    for message in conversation.messages:
        participant = participant_resolver.resolve(message.author)
        speaker = (
            participant.role or participant.discord_name or message.author.display_name
        )
        lines.append(f"{message.created_at} {speaker}: {message.content}")
    return "\n".join(lines)


def render_save_response(result: SaveCommandResult) -> str:
    """Render a concise Korean response without exposing raw error text."""

    if not result.ok:
        return _ERROR_RESPONSES.get(result.error, _ERROR_RESPONSES["save_failed"])
    if result.status == "unchanged":
        return f"새로 저장할 메시지가 없습니다.\n- 문서: {result.canonical_path}"

    classification = "회의" if result.classification == "meeting" else "대화"
    return (
        "저장 완료\n"
        f"- 유형: {classification}\n"
        f"- 새 메시지: {result.new_message_count}개\n"
        f"- 문서: {result.canonical_path}\n"
        f"- 요약: {result.summary}"
    )
