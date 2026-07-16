"""Transport-neutral orchestration for saving Discord thread conversations."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .conversation_summary import HermesConversationSummarizer
from .discord_conversation import DiscordConversation, ParticipantResolver
from .discord_history import (
    DiscordCollectionLifecycleLock,
    DiscordHistoryClient,
    DiscordHistoryError,
)
from .hermes_command_context import HermesCommandContext
from .knowledge import sanitize_knowledge_text
from .obsidian_conversations import ObsidianConversationStore
from .store import MeetingRunStore, StoreError

_ERROR_RESPONSES = {
    "discord_only": (
        "/archive는 Discord에서만 사용할 수 있습니다. Discord에서 /archive를 다시 "
        "실행해주세요."
    ),
    "thread_required": (
        "대화를 저장하려면 Discord 스레드 안에서 /archive를 다시 실행해주세요."
    ),
    "dm_boundary_unavailable": (
        "현재 Hermes는 DM 세션 시작 경계를 제공하지 않아 저장하지 않았습니다. "
        "DM 세션 시작 경계를 지원하는 Hermes로 업그레이드한 뒤 다시 시도해주세요."
    ),
    "invocation_boundary_unavailable": (
        "명령 실행 시점 경계를 확인할 수 없어 저장하지 않았습니다. Hermes gateway를 "
        "재시작한 뒤 다시 시도해주세요."
    ),
    "missing_discord_token": (
        "Discord 봇 토큰이 설정되지 않았습니다. 토큰을 설정한 뒤 /archive를 다시 "
        "실행해주세요."
    ),
    "history_unavailable": (
        "Discord 대화 기록을 불러오지 못했습니다. 잠시 후 /archive를 다시 시도하고, "
        "계속 실패하면 봇의 기록 읽기 권한을 확인해주세요."
    ),
    "vault_unavailable": (
        "Obsidian 보관함을 사용할 수 없습니다. 보관함 경로와 쓰기 권한을 확인한 뒤 "
        "다시 시도해주세요."
    ),
    "save_failed": (
        "대화를 저장하지 못했습니다. 잠시 후 /archive를 다시 시도해주세요."
    ),
}
_SUCCESS_STATUS_RESPONSES = {
    "created": "저장 완료",
    "updated": "저장 업데이트 완료",
    "unchanged": "새로 저장할 메시지가 없습니다.",
}
_SUMMARY_ERRORS = (OSError, RuntimeError, ValueError)
_T = TypeVar("_T")


@dataclass(frozen=True)
class SaveCommandResult:
    ok: bool
    status: str = ""
    classification: str = ""
    new_message_count: int = 0
    canonical_path: str = ""
    title: str = ""
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
    source_id = context.thread_id or context.chat_id
    if not source_id:
        return SaveCommandResult(ok=False, error="thread_required")

    source_kind = "thread" if context.thread_id else ""
    if not source_kind:
        try:
            source_kind = await asyncio.to_thread(
                history_client.classify_source,
                source_id,
            )
        except DiscordHistoryError:
            return SaveCommandResult(ok=False, error="history_unavailable")
    if source_kind == "guild_channel":
        return SaveCommandResult(ok=False, error="thread_required")
    if source_kind == "dm" and not context.session_start_message_id:
        return SaveCommandResult(ok=False, error="dm_boundary_unavailable")
    if not context.invocation_message_id:
        return SaveCommandResult(
            ok=False,
            error="invocation_boundary_unavailable",
        )

    lifecycle_lock = history_client.collection_lifecycle_lock(
        source_id,
        **(
            {"after_message_id": context.session_start_message_id}
            if source_kind == "dm"
            else {}
        ),
    )
    try:
        await _acquire_lifecycle_lock(lifecycle_lock)
    except (OSError, RuntimeError, TimeoutError):
        return SaveCommandResult(ok=False, error="history_unavailable")
    try:
        return await _run_save_lifecycle(
            context=context,
            source_id=source_id,
            source_kind=source_kind,
            history_client=history_client,
            meeting_store=meeting_store,
            participant_resolver=participant_resolver,
            summarizer=summarizer,
            obsidian_store=obsidian_store,
        )
    finally:
        await _settled_to_thread(lifecycle_lock.release)


async def _acquire_lifecycle_lock(
    lifecycle_lock: DiscordCollectionLifecycleLock,
) -> None:
    state_lock = threading.Lock()
    cancelled = False
    acquired = False

    def acquire_or_release() -> None:
        nonlocal acquired
        lifecycle_lock.acquire()
        with state_lock:
            release_after_acquire = cancelled
            acquired = not release_after_acquire
        if release_after_acquire:
            lifecycle_lock.release()

    acquisition = asyncio.create_task(asyncio.to_thread(acquire_or_release))
    acquisition.add_done_callback(_consume_background_task_exception)
    try:
        await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        with state_lock:
            cancelled = True
            release_acquired = acquired
            acquired = False
        if release_acquired:
            await _settled_to_thread(lifecycle_lock.release)
        raise


def _consume_background_task_exception(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


async def _settled_to_thread(
    function: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation_requested = False
    while True:
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.cancelled():
                raise
            cancellation_requested = True
            continue
        except BaseException:
            if cancellation_requested:
                raise asyncio.CancelledError from None
            raise
        if cancellation_requested:
            raise asyncio.CancelledError
        return result


async def _run_save_lifecycle(
    *,
    context: HermesCommandContext,
    source_id: str,
    source_kind: str,
    history_client: DiscordHistoryClient,
    meeting_store: MeetingRunStore,
    participant_resolver: ParticipantResolver,
    summarizer: HermesConversationSummarizer,
    obsidian_store: ObsidianConversationStore,
) -> SaveCommandResult:
    try:
        conversation = await _settled_to_thread(
            history_client.fetch_conversation,
            source_id,
            cutoff_message_id=context.invocation_message_id,
            **(
                {
                    "after_message_id": context.session_start_message_id,
                    "expected_kind": "dm",
                }
                if source_kind == "dm"
                else {}
            ),
        )
    except DiscordHistoryError as exc:
        code = exc.code
        if code in {"missing_discord_token", "thread_required"}:
            return SaveCommandResult(ok=False, error=code)
        return SaveCommandResult(ok=False, error="history_unavailable")

    meeting_run = None
    if source_kind == "thread":
        try:
            meeting_run = await _settled_to_thread(
                meeting_store.find_by_discord_thread_id, source_id
            )
        except (OSError, StoreError):
            return SaveCommandResult(ok=False, error="save_failed")

    transcript = render_summary_input(conversation, participant_resolver)
    try:
        summary = await summarizer.summarize(transcript)
    except _SUMMARY_ERRORS:
        return SaveCommandResult(ok=False, error="save_failed")

    try:
        saved = await _settled_to_thread(
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

    try:
        await _settled_to_thread(
            history_client.discard_collection_checkpoint,
            source_id,
            **(
                {"after_message_id": context.session_start_message_id}
                if source_kind == "dm"
                else {}
            ),
        )
    except OSError:
        return SaveCommandResult(ok=False, error="save_failed")

    return SaveCommandResult(
        ok=True,
        status=saved.status,
        classification=saved.classification,
        new_message_count=saved.new_message_count,
        canonical_path=saved.canonical_path,
        title=saved.document_title,
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
        lines.extend(
            f"{message.created_at} {speaker} attachment "
            f"{attachment.filename}: {attachment.url}"
            for attachment in message.attachments
        )
    return "\n".join(lines)


def render_save_response(result: SaveCommandResult) -> str:
    """Render a concise Korean response without exposing raw error text."""

    if not result.ok:
        return _ERROR_RESPONSES.get(result.error, _ERROR_RESPONSES["save_failed"])

    status = _SUCCESS_STATUS_RESPONSES.get(result.status, "저장 완료")
    classification = "회의" if result.classification == "meeting" else "대화"
    title = sanitize_knowledge_text(result.title)
    return (
        f"{status}\n"
        f"- 유형: {classification}\n"
        f"- 제목: {title}\n"
        f"- 새 메시지: {result.new_message_count}개\n"
        f"- 문서: {result.canonical_path}\n"
        f"- 요약: {result.summary}"
    )
