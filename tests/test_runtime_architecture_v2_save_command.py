from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.runtime_architecture_v2.conversation_summary import ConversationSummary
from src.runtime_architecture_v2.discord_conversation import (
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
)
from src.runtime_architecture_v2.discord_history import DiscordHistoryError
from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext
from src.runtime_architecture_v2.obsidian_conversations import ObsidianSaveResult
from src.runtime_architecture_v2.save_command import (
    SaveCommandResult,
    render_save_response,
    run_save_command,
)
from src.runtime_architecture_v2.schemas import MeetingRun
from src.runtime_architecture_v2.store import StoreError


def _save_conversation() -> DiscordConversation:
    return DiscordConversation(
        guild_id="1",
        parent_channel_id="100",
        thread_id="200",
        thread_name="콘텐츠 전략",
        visibility="guild",
        messages=(
            DiscordMessage(
                message_id="1",
                created_at="2026-07-13T01:00:00+00:00",
                content="3편 제작으로 결정",
                author=DiscordAuthor("300", "KBM"),
            ),
        ),
    )


def _save_meeting() -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id="mr-1",
        trigger_text="콘텐츠 전략",
        user_id="300",
        channel_id="100",
        thread_id="200",
    )


def _dependencies(
    conversation: DiscordConversation,
    meeting_run: MeetingRun | None = None,
) -> tuple[Mock, Mock, Mock, Mock, Mock]:
    history = Mock()
    history.fetch_conversation.return_value = conversation
    meetings = Mock()
    meetings.find_by_discord_thread_id.return_value = meeting_run
    resolver = Mock()
    resolver.resolve.return_value = Mock(role="", discord_name="KBM")
    summarizer = Mock()
    summarizer.summarize = AsyncMock(
        return_value=ConversationSummary(summary="콘텐츠 방향을 합의했다.")
    )
    obsidian = Mock()
    obsidian.save.return_value = ObsidianSaveResult(
        status="created",
        classification="meeting" if meeting_run else "conversation",
        new_message_count=len(conversation.messages),
        snapshot_path="raw/chat-logs/snapshot.md",
        canonical_path="wiki/conversations/page.md",
        one_line_summary="콘텐츠 방향을 합의했다.",
    )
    return history, meetings, resolver, summarizer, obsidian


def _thread_context() -> HermesCommandContext:
    return HermesCommandContext(platform="discord", chat_id="200", thread_id="200")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "expected_error"),
    [
        (HermesCommandContext(platform="telegram", chat_id="200"), "discord_only"),
        (HermesCommandContext(platform="discord", chat_id="100"), "thread_required"),
    ],
)
async def test_save_rejects_non_discord_and_non_thread_contexts(
    context: HermesCommandContext,
    expected_error: str,
) -> None:
    history = Mock()

    result = await run_save_command(
        context=context,
        history_client=history,
        meeting_store=Mock(),
        participant_resolver=Mock(),
        summarizer=Mock(),
        obsidian_store=Mock(),
    )

    assert result == SaveCommandResult(ok=False, error=expected_error)
    history.fetch_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_save_classifies_linked_thread_as_meeting() -> None:
    conversation = _save_conversation()
    meeting = _save_meeting()
    history, meetings, resolver, summarizer, obsidian = _dependencies(
        conversation, meeting
    )

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result.ok is True
    assert result.classification == "meeting"
    assert obsidian.save.call_args.kwargs["meeting_run"] == meeting
    transcript = summarizer.summarize.await_args.args[0]
    assert "KBM: 3편 제작으로 결정" in transcript


@pytest.mark.asyncio
async def test_save_classifies_unlinked_thread_as_conversation() -> None:
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result.classification == "conversation"
    assert obsidian.save.call_args.kwargs["meeting_run"] is None


@pytest.mark.asyncio
async def test_fallback_summary_still_persists() -> None:
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    summarizer.summarize.return_value = ConversationSummary(summary="3편 제작으로 결정")

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result.ok is True
    obsidian.save.assert_called_once()


@pytest.mark.asyncio
async def test_sync_history_lookup_and_save_run_through_to_thread(monkeypatch) -> None:
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    real_to_thread = asyncio.to_thread
    to_thread = AsyncMock(side_effect=real_to_thread)
    monkeypatch.setattr(asyncio, "to_thread", to_thread)

    await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert [call.args[0] for call in to_thread.await_args_list] == [
        history.fetch_conversation,
        meetings.find_by_discord_thread_id,
        obsidian.save,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("history_error", "expected_error"),
    [
        ("missing_discord_token", "missing_discord_token"),
        ("thread_required", "thread_required"),
        ("discord_transport_error", "history_unavailable"),
        ("discord_http_status_403", "history_unavailable"),
    ],
)
async def test_history_errors_map_to_sanitized_command_errors(
    history_error: str,
    expected_error: str,
) -> None:
    history = Mock()
    history.fetch_conversation.side_effect = DiscordHistoryError(history_error)

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=Mock(),
        participant_resolver=Mock(),
        summarizer=Mock(),
        obsidian_store=Mock(),
    )

    assert result == SaveCommandResult(ok=False, error=expected_error)


@pytest.mark.asyncio
async def test_meeting_store_error_maps_to_save_failed_without_raw_text() -> None:
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    meetings.find_by_discord_thread_id.side_effect = StoreError(
        code="corrupt_meeting_run",
        message="secret-token",
    )

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result == SaveCommandResult(ok=False, error="save_failed")
    assert "secret-token" not in render_save_response(result)


@pytest.mark.asyncio
async def test_summary_error_maps_to_save_failed_without_persisting() -> None:
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    summarizer.summarize.side_effect = RuntimeError("secret-token")

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result == SaveCommandResult(ok=False, error="save_failed")
    obsidian.save.assert_not_called()
    assert "secret-token" not in render_save_response(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("storage_error", "expected_error"),
    [
        (OSError("vault path includes secret-token"), "vault_unavailable"),
        (ValueError("invalid checkpoint secret-token"), "save_failed"),
    ],
)
async def test_storage_errors_map_without_leaking_raw_text(
    storage_error: Exception,
    expected_error: str,
) -> None:
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    obsidian.save.side_effect = storage_error

    result = await run_save_command(
        context=_thread_context(),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result == SaveCommandResult(ok=False, error=expected_error)
    assert "secret-token" not in render_save_response(result)


def test_response_reports_path_count_summary_and_status() -> None:
    result = SaveCommandResult(
        ok=True,
        status="created",
        classification="meeting",
        new_message_count=42,
        canonical_path="wiki/conversations/page.md",
        summary="쇼츠 3편 제작 방향을 합의했습니다.",
    )

    rendered = render_save_response(result)

    assert rendered == (
        "저장 완료\n"
        "- 유형: 회의\n"
        "- 새 메시지: 42개\n"
        "- 문서: wiki/conversations/page.md\n"
        "- 요약: 쇼츠 3편 제작 방향을 합의했습니다."
    )


def test_unchanged_response_reports_existing_path() -> None:
    result = SaveCommandResult(
        ok=True,
        status="unchanged",
        classification="conversation",
        canonical_path="wiki/conversations/page.md",
    )

    rendered = render_save_response(result)

    assert "새로 저장할 메시지가 없습니다" in rendered
    assert "wiki/conversations/page.md" in rendered


def test_thread_required_response_is_concise_korean_guidance() -> None:
    result = SaveCommandResult(ok=False, error="thread_required")

    assert render_save_response(result) == (
        "대화를 저장하려면 Discord 스레드 안에서 /save를 실행해주세요."
    )


def test_unknown_error_text_is_never_rendered() -> None:
    rendered = render_save_response(
        SaveCommandResult(ok=False, error="secret-token raw exception")
    )

    assert rendered == "대화를 저장하지 못했습니다."
    assert "secret-token" not in rendered
