from __future__ import annotations

import asyncio
import json
import multiprocessing
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.runtime_architecture_v2.conversation_summary import (
    ConversationSummary,
    HermesConversationSummarizer,
)
from src.runtime_architecture_v2.discord_conversation import (
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
    DiscordSourceIdentity,
    ParticipantResolver,
)
from src.runtime_architecture_v2.discord_history import (
    DiscordHistoryClient,
    DiscordHistoryError,
)
from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext
from src.runtime_architecture_v2.obsidian_conversations import (
    ObsidianConversationStore,
    ObsidianSaveResult,
)
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
        document_title=conversation.thread_name,
        one_line_summary="콘텐츠 방향을 합의했다.",
    )
    return history, meetings, resolver, summarizer, obsidian


def _thread_context() -> HermesCommandContext:
    return HermesCommandContext(
        platform="discord",
        chat_id="200",
        thread_id="200",
        session_id="session-1",
        invocation_message_id="250",
    )


class _ConcurrentMeetingStore:
    def find_by_discord_thread_id(self, _source_id):
        return None


class _ConcurrentSummarizer:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def summarize(self, _transcript):
        if self._fail:
            raise RuntimeError("expected summary failure")
        return ConversationSummary(summary="Concurrent save.")


class _ConcurrentObsidianStore:
    def __init__(self, persist_started, persist_release) -> None:
        self._persist_started = persist_started
        self._persist_release = persist_release

    def save(self, **_kwargs):
        self._persist_started.set()
        if not self._persist_release.wait(timeout=15):
            raise TimeoutError("test persist release timeout")
        return ObsidianSaveResult(
            status="created",
            classification="conversation",
            new_message_count=1,
            snapshot_path="raw/chat-logs/concurrent.md",
            canonical_path="wiki/conversations/concurrent.md",
            document_title="Concurrent",
            one_line_summary="Concurrent save.",
        )


class _BoundedLifecycleLock:
    def __init__(self, shared_lock: threading.Lock) -> None:
        self._shared_lock = shared_lock
        self._state_lock = threading.Lock()
        self._acquired = False
        self.acquire_started = threading.Event()
        self.acquired = threading.Event()
        self.released = threading.Event()

    def acquire(self) -> None:
        self.acquire_started.set()
        if not self._shared_lock.acquire(timeout=0.75):
            raise TimeoutError("bounded test lifecycle lock timeout")
        with self._state_lock:
            self._acquired = True
        self.acquired.set()

    def release(self) -> None:
        with self._state_lock:
            if not self._acquired:
                return
            self._acquired = False
        self._shared_lock.release()
        self.released.set()


def _concurrent_save_worker(
    *,
    runtime_root,
    cutoff,
    fail_after_collection,
    collected,
    persist_started,
    persist_release,
    result_queue,
):
    runtime_root = Path(runtime_root)

    def request(_method, path, _query):
        if path == "/channels/200":
            return {
                "id": "200",
                "type": 11,
                "name": "Concurrent",
                "parent_id": "100",
                "guild_id": "1",
            }
        collected.set()
        return [
            {
                "id": str(int(cutoff) - 25),
                "timestamp": "2026-07-13T01:00:00+00:00",
                "content": f"message for {cutoff}",
                "author": {"id": "300", "username": "KBM"},
            }
        ]

    result = asyncio.run(
        run_save_command(
            context=HermesCommandContext(
                platform="discord",
                chat_id="200",
                thread_id="200",
                session_id=f"session-{cutoff}",
                invocation_message_id=str(cutoff),
            ),
            history_client=DiscordHistoryClient(
                token="secret",
                request_json=request,
                checkpoint_root=(
                    runtime_root / "runtime" / "discord_save" / "collection"
                ),
            ),
            meeting_store=_ConcurrentMeetingStore(),
            participant_resolver=ParticipantResolver({}),
            summarizer=_ConcurrentSummarizer(fail=fail_after_collection),
            obsidian_store=_ConcurrentObsidianStore(
                persist_started,
                persist_release,
            ),
        )
    )
    result_queue.put((str(cutoff), result.ok, result.error))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "expected_error"),
    [
        (HermesCommandContext(platform="telegram", chat_id="200"), "discord_only"),
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
async def test_save_rejects_guild_channel_after_reliable_channel_classification():
    history = Mock()
    history.classify_source.return_value = "guild_channel"

    result = await run_save_command(
        context=HermesCommandContext(platform="discord", chat_id="100"),
        history_client=history,
        meeting_store=Mock(),
        participant_resolver=Mock(),
        summarizer=Mock(),
        obsidian_store=Mock(),
    )

    assert result == SaveCommandResult(ok=False, error="thread_required")
    history.classify_source.assert_called_once_with("100")
    history.fetch_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_save_rejects_dm_without_boundary_or_side_effects():
    history = Mock()
    history.classify_source.return_value = "dm"
    summarizer = Mock()
    obsidian = Mock()

    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord",
            chat_id="900",
            session_id="session-dm",
            invocation_message_id="950",
        ),
        history_client=history,
        meeting_store=Mock(),
        participant_resolver=Mock(),
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result == SaveCommandResult(ok=False, error="dm_boundary_unavailable")
    history.fetch_conversation.assert_not_called()
    summarizer.summarize.assert_not_called()
    obsidian.save.assert_not_called()
    rendered = render_save_response(result)
    assert "DM 세션 시작 경계" in rendered
    assert "스레드" not in rendered


@pytest.mark.asyncio
async def test_save_accepts_private_dm_with_explicit_session_start_boundary():
    conversation = _save_conversation()
    conversation = DiscordConversation(
        guild_id="",
        parent_channel_id="",
        thread_id="900",
        thread_name="Direct message",
        visibility="private",
        messages=conversation.messages,
    )
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    history.classify_source.return_value = "dm"

    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord",
            chat_id="900",
            session_id="session-dm",
            invocation_message_id="950",
            session_start_message_id="800",
            source_kind="dm",
        ),
        history_client=history,
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert result.ok is True
    history.fetch_conversation.assert_called_once_with(
        "900",
        cutoff_message_id="950",
        after_message_id="800",
        expected_kind="dm",
    )
    assert obsidian.save.call_args.kwargs["conversation"].visibility == "private"


@pytest.mark.asyncio
async def test_save_rejects_thread_when_invocation_cutoff_is_unavailable():
    history = Mock()

    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord",
            chat_id="200",
            thread_id="200",
            session_id="session-1",
        ),
        history_client=history,
        meeting_store=Mock(),
        participant_resolver=Mock(),
        summarizer=Mock(),
        obsidian_store=Mock(),
    )

    assert result == SaveCommandResult(
        ok=False,
        error="invocation_boundary_unavailable",
    )
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
    history.fetch_conversation.assert_called_once_with(
        "200",
        cutoff_message_id="250",
    )
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

    lifecycle_lock = history.collection_lifecycle_lock.return_value
    threaded_calls = [call.args[0] for call in to_thread.await_args_list]
    assert threaded_calls[0].__name__ == "acquire_or_release"
    assert threaded_calls[1:] == [
        history.fetch_conversation,
        meetings.find_by_discord_thread_id,
        obsidian.save,
        history.discard_collection_checkpoint,
        lifecycle_lock.release,
    ]
    lifecycle_lock.acquire.assert_called_once_with()


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
        title="콘텐츠 전략",
        summary="쇼츠 3편 제작 방향을 합의했습니다.",
    )

    rendered = render_save_response(result)

    assert rendered == (
        "저장 완료\n"
        "- 유형: 회의\n"
        "- 제목: 콘텐츠 전략\n"
        "- 새 메시지: 42개\n"
        "- 문서: wiki/conversations/page.md\n"
        "- 요약: 쇼츠 3편 제작 방향을 합의했습니다."
    )


def test_updated_response_reports_all_success_details_with_distinct_status() -> None:
    result = SaveCommandResult(
        ok=True,
        status="updated",
        classification="conversation",
        new_message_count=7,
        canonical_path="wiki/conversations/page.md",
        title="후속 전략",
        summary="후속 결정을 반영했습니다.",
    )

    rendered = render_save_response(result)

    assert rendered == (
        "저장 업데이트 완료\n"
        "- 유형: 대화\n"
        "- 제목: 후속 전략\n"
        "- 새 메시지: 7개\n"
        "- 문서: wiki/conversations/page.md\n"
        "- 요약: 후속 결정을 반영했습니다."
    )


def test_unchanged_response_reports_all_success_details_and_no_new_notice() -> None:
    result = SaveCommandResult(
        ok=True,
        status="unchanged",
        classification="conversation",
        new_message_count=0,
        canonical_path="wiki/conversations/page.md",
        title="기존 전략",
        summary="기존 콘텐츠 방향을 유지합니다.",
    )

    rendered = render_save_response(result)

    assert rendered == (
        "새로 저장할 메시지가 없습니다.\n"
        "- 유형: 대화\n"
        "- 제목: 기존 전략\n"
        "- 새 메시지: 0개\n"
        "- 문서: wiki/conversations/page.md\n"
        "- 요약: 기존 콘텐츠 방향을 유지합니다."
    )


def test_thread_required_response_is_concise_korean_guidance() -> None:
    result = SaveCommandResult(ok=False, error="thread_required")

    assert render_save_response(result) == (
        "대화를 저장하려면 Discord 스레드 안에서 /save를 다시 실행해주세요."
    )


@pytest.mark.parametrize(
    ("error", "retry_guidance"),
    [
        ("discord_only", "Discord에서 /save를 다시 실행"),
        ("missing_discord_token", "토큰을 설정한 뒤 /save를 다시 실행"),
        ("history_unavailable", "잠시 후 /save를 다시 시도"),
        ("vault_unavailable", "보관함 경로와 쓰기 권한을 확인한 뒤 다시 시도"),
        ("save_failed", "잠시 후 /save를 다시 시도"),
        ("invocation_boundary_unavailable", "gateway를 재시작한 뒤 다시 시도"),
    ],
)
def test_failure_responses_include_concrete_retry_guidance(
    error: str,
    retry_guidance: str,
) -> None:
    assert retry_guidance in render_save_response(
        SaveCommandResult(ok=False, error=error)
    )


def test_unknown_error_text_is_never_rendered() -> None:
    rendered = render_save_response(
        SaveCommandResult(ok=False, error="secret-token raw exception")
    )

    assert rendered == "대화를 저장하지 못했습니다. 잠시 후 /save를 다시 시도해주세요."
    assert "secret-token" not in rendered


@pytest.mark.asyncio
async def test_url_credentials_are_removed_end_to_end_before_llm_and_vault(tmp_path):
    message_url = "https://alice:p%40ss@example.test/private"
    attachment_url = "https://bob%3Aencoded%40cdn.example.test/file.pdf"

    def request(_method, path, _query):
        if path == "/channels/200":
            return {
                "id": "200",
                "type": 12,
                "name": "Private plan",
                "parent_id": "100",
                "guild_id": "1",
            }
        return [
            {
                "id": "225",
                "timestamp": "2026-07-13T01:00:00+00:00",
                "content": f"Review {message_url}",
                "author": {"id": "300", "username": "KBM"},
                "attachments": [
                    {
                        "id": "500",
                        "filename": "brief.pdf",
                        "content_type": "application/pdf",
                        "size": 42,
                        "url": attachment_url,
                    }
                ],
            }
        ]

    llm = AsyncMock()
    llm.acomplete_structured.return_value = SimpleNamespace(
        parsed={
            "summary": "See https://summary:secret@example.test/result",
            "key_ideas": [],
            "decisions": [],
            "unresolved_questions": [],
            "action_items": [],
            "user_perspective": "",
        }
    )
    result = await run_save_command(
        context=_thread_context(),
        history_client=DiscordHistoryClient(
            token="secret",
            request_json=request,
            checkpoint_root=tmp_path / "runtime" / "discord_save" / "collection",
        ),
        meeting_store=Mock(find_by_discord_thread_id=Mock(return_value=None)),
        participant_resolver=ParticipantResolver({}),
        summarizer=HermesConversationSummarizer(llm),
        obsidian_store=ObsidianConversationStore(
            vault_root=tmp_path / "vault",
            runtime_root=tmp_path,
        ),
    )

    assert result.ok is True
    exact_llm_input = llm.acomplete_structured.await_args.kwargs["input"]
    written = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "vault").rglob("*.md")
    )
    checkpoint = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runtime" / "discord_save").rglob("*.json")
    )
    combined = str(exact_llm_input) + written + checkpoint
    for credential in ("alice", "p%40ss", "bob", "encoded", "summary:secret"):
        assert credential not in combined
    assert "https://example.test/private" in combined
    assert "https://cdn.example.test/file.pdf" in combined
    assert "https://example.test/result" in written
    assert 'visibility: "private"' in written


@pytest.mark.asyncio
@pytest.mark.parametrize("durable_status", ["created", "unchanged"])
async def test_durable_save_removes_full_collection_checkpoint(
    tmp_path, durable_status
):
    checkpoint_root = tmp_path / "runtime" / "discord_save" / "collection"

    def request(_method, path, _query):
        if path == "/channels/200":
            return {
                "id": "200",
                "type": 11,
                "name": "Checkpoint cleanup",
                "parent_id": "100",
                "guild_id": "1",
            }
        return [
            {
                "id": "225",
                "timestamp": "2026-07-13T01:00:00+00:00",
                "content": "durable message",
                "author": {"id": "300", "username": "KBM"},
            }
        ]

    history = DiscordHistoryClient(
        token="secret",
        request_json=request,
        checkpoint_root=checkpoint_root,
    )
    _, meetings, resolver, summarizer, obsidian = _dependencies(_save_conversation())
    obsidian.save.return_value = ObsidianSaveResult(
        status=durable_status,
        classification="conversation",
        new_message_count=0 if durable_status == "unchanged" else 1,
        snapshot_path="raw/chat-logs/snapshot.md",
        canonical_path="wiki/conversations/page.md",
        document_title="Checkpoint cleanup",
        one_line_summary="Saved.",
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
    assert list(checkpoint_root.glob("*.json")) == []


@pytest.mark.asyncio
async def test_failed_old_cutoff_is_adopted_by_later_save_then_cleaned(tmp_path):
    checkpoint_root = tmp_path / "runtime" / "discord_save" / "collection"

    def first_request(_method, path, query):
        if path == "/channels/200":
            return {
                "id": "200",
                "type": 11,
                "name": "Resume",
                "parent_id": "100",
                "guild_id": "1",
            }
        if query["before"] == "250":
            return [
                {
                    "id": str(message_id),
                    "timestamp": "2026-07-13T01:00:00+00:00",
                    "content": f"old {message_id}",
                    "author": {"id": "300", "username": "KBM"},
                }
                for message_id in range(249, 149, -1)
            ]
        raise DiscordHistoryError("discord_http_status_503")

    first = await run_save_command(
        context=_thread_context(),
        history_client=DiscordHistoryClient(
            token="secret",
            request_json=first_request,
            checkpoint_root=checkpoint_root,
            max_retries=0,
        ),
        meeting_store=Mock(),
        participant_resolver=ParticipantResolver({}),
        summarizer=Mock(),
        obsidian_store=Mock(),
    )
    assert first == SaveCommandResult(ok=False, error="history_unavailable")
    assert len(list(checkpoint_root.glob("*.json"))) == 1

    resumed_queries = []

    def resumed_request(_method, path, query):
        if path == "/channels/200":
            return {
                "id": "200",
                "type": 11,
                "name": "Resume",
                "parent_id": "100",
                "guild_id": "1",
            }
        resumed_queries.append(query)
        if query["before"] == "300":
            ids = range(299, 199, -1)
        elif query["before"] == "150":
            ids = [149]
        else:
            raise AssertionError(f"unexpected query: {query}")
        return [
            {
                "id": str(message_id),
                "timestamp": "2026-07-13T01:00:00+00:00",
                "content": f"new {message_id}",
                "author": {"id": "300", "username": "KBM"},
            }
            for message_id in ids
        ]

    conversation = _save_conversation()
    _, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    second = await run_save_command(
        context=HermesCommandContext(
            platform="discord",
            chat_id="200",
            thread_id="200",
            session_id="session-2",
            invocation_message_id="300",
        ),
        history_client=DiscordHistoryClient(
            token="secret",
            request_json=resumed_request,
            checkpoint_root=checkpoint_root,
        ),
        meeting_store=meetings,
        participant_resolver=resolver,
        summarizer=summarizer,
        obsidian_store=obsidian,
    )

    assert second.ok is True
    assert resumed_queries == [
        {"limit": "100", "before": "300"},
        {"limit": "100", "before": "150"},
    ]
    persisted = obsidian.save.call_args.kwargs["conversation"]
    assert len(persisted.messages) == 151
    assert list(checkpoint_root.glob("*.json")) == []


@pytest.mark.asyncio
async def test_explicit_private_dm_boundary_persists_through_real_store(tmp_path):
    def request(_method, path, _query):
        if path == "/channels/900":
            return {"id": "900", "type": 1, "name": "Direct message"}
        return [
            {
                "id": "900",
                "timestamp": "2026-07-13T01:00:00+00:00",
                "content": "private session message",
                "author": {"id": "300", "username": "KBM"},
            }
        ]

    llm = AsyncMock()
    llm.acomplete_structured.return_value = SimpleNamespace(
        parsed={
            "summary": "Private session saved.",
            "key_ideas": [],
            "decisions": [],
            "unresolved_questions": [],
            "action_items": [],
            "user_perspective": "",
        }
    )
    history = DiscordHistoryClient(
        token="secret",
        request_json=request,
        checkpoint_root=tmp_path / "runtime" / "discord_save" / "collection",
    )
    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord",
            chat_id="900",
            session_id="future-supported-dm",
            invocation_message_id="950",
            session_start_message_id="800",
            source_kind="dm",
        ),
        history_client=history,
        meeting_store=Mock(),
        participant_resolver=ParticipantResolver({}),
        summarizer=HermesConversationSummarizer(llm),
        obsidian_store=ObsidianConversationStore(
            vault_root=tmp_path / "vault",
            runtime_root=tmp_path,
        ),
    )

    assert result.ok is True
    collected = history.fetch_conversation(
        "900",
        cutoff_message_id="950",
        after_message_id="800",
        expected_kind="dm",
    )
    assert collected.source_identity == DiscordSourceIdentity.private_dm("900")
    written = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "vault").rglob("*.md")
    )
    assert 'discord_source_kind: "dm"' in written
    assert 'discord_source_id: "900"' in written
    assert 'visibility: "private"' in written


def _cancellation_test_dependencies():
    history, meetings, resolver, summarizer, obsidian = _dependencies(
        _save_conversation()
    )
    shared_lock = threading.Lock()
    generated_locks = []

    def create_lock(_source_id, **_kwargs):
        lifecycle_lock = _BoundedLifecycleLock(shared_lock)
        generated_locks.append(lifecycle_lock)
        return lifecycle_lock

    history.collection_lifecycle_lock.side_effect = create_lock
    owner = _BoundedLifecycleLock(shared_lock)
    owner.acquire()
    return (
        history,
        meetings,
        resolver,
        summarizer,
        obsidian,
        owner,
        generated_locks,
    )


async def _cancel_waiting_save(
    history,
    meetings,
    resolver,
    summarizer,
    obsidian,
    generated_locks,
):
    waiting = asyncio.create_task(
        run_save_command(
            context=_thread_context(),
            history_client=history,
            meeting_store=meetings,
            participant_resolver=resolver,
            summarizer=summarizer,
            obsidian_store=obsidian,
        )
    )
    while not generated_locks:
        await asyncio.sleep(0)
    lifecycle_lock = generated_locks[0]
    assert await asyncio.to_thread(lifecycle_lock.acquire_started.wait, 2)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    return lifecycle_lock


@pytest.mark.asyncio
async def test_cancelling_waiting_save_releases_late_acquired_lifecycle_lock():
    dependencies = _cancellation_test_dependencies()
    history, meetings, resolver, summarizer, obsidian, owner, generated_locks = (
        dependencies
    )
    try:
        cancelled_lock = await _cancel_waiting_save(
            history,
            meetings,
            resolver,
            summarizer,
            obsidian,
            generated_locks,
        )
        owner.release()

        assert await asyncio.to_thread(cancelled_lock.acquired.wait, 2)
        assert await asyncio.to_thread(cancelled_lock.released.wait, 2)
    finally:
        owner.release()
        for lifecycle_lock in generated_locks:
            lifecycle_lock.release()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_block_later_save_for_same_source():
    dependencies = _cancellation_test_dependencies()
    history, meetings, resolver, summarizer, obsidian, owner, generated_locks = (
        dependencies
    )
    try:
        cancelled_lock = await _cancel_waiting_save(
            history,
            meetings,
            resolver,
            summarizer,
            obsidian,
            generated_locks,
        )
        owner.release()
        assert await asyncio.to_thread(cancelled_lock.acquired.wait, 2)

        reused = await run_save_command(
            context=_thread_context(),
            history_client=history,
            meeting_store=meetings,
            participant_resolver=resolver,
            summarizer=summarizer,
            obsidian_store=obsidian,
        )

        assert reused.ok is True
    finally:
        owner.release()
        for lifecycle_lock in generated_locks:
            lifecycle_lock.release()


def test_same_source_threaded_lifecycle_preserves_failed_later_generation(tmp_path):
    first_collected = threading.Event()
    second_collected = threading.Event()
    persist_started = threading.Event()
    persist_release = threading.Event()
    results = queue.Queue()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            _concurrent_save_worker,
            runtime_root=tmp_path,
            cutoff="250",
            fail_after_collection=False,
            collected=first_collected,
            persist_started=persist_started,
            persist_release=persist_release,
            result_queue=results,
        )
        assert persist_started.wait(timeout=10)
        second = executor.submit(
            _concurrent_save_worker,
            runtime_root=tmp_path,
            cutoff="300",
            fail_after_collection=True,
            collected=second_collected,
            persist_started=threading.Event(),
            persist_release=threading.Event(),
            result_queue=results,
        )

        second_was_blocked = second_collected.wait(timeout=0.5) is False
        persist_release.set()
        first.result(timeout=15)
        second.result(timeout=15)

    assert second_was_blocked
    assert sorted(results.get_nowait() for _ in range(2)) == [
        ("250", True, ""),
        ("300", False, "save_failed"),
    ]
    checkpoint_path = (
        tmp_path / "runtime" / "discord_save" / "collection" / "200__start.json"
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["cutoff_message_id"] == "300"


def test_same_source_multiprocess_lifecycle_preserves_failed_later_generation(
    tmp_path,
):
    context = multiprocessing.get_context("spawn")
    first_collected = context.Event()
    second_collected = context.Event()
    persist_started = context.Event()
    persist_release = context.Event()
    unused_started = context.Event()
    unused_release = context.Event()
    results = context.Queue()
    first = context.Process(
        target=_concurrent_save_worker,
        kwargs={
            "runtime_root": str(tmp_path),
            "cutoff": "250",
            "fail_after_collection": False,
            "collected": first_collected,
            "persist_started": persist_started,
            "persist_release": persist_release,
            "result_queue": results,
        },
    )
    second = context.Process(
        target=_concurrent_save_worker,
        kwargs={
            "runtime_root": str(tmp_path),
            "cutoff": "300",
            "fail_after_collection": True,
            "collected": second_collected,
            "persist_started": unused_started,
            "persist_release": unused_release,
            "result_queue": results,
        },
    )

    first.start()
    assert persist_started.wait(timeout=15)
    second.start()
    try:
        assert second_collected.wait(timeout=0.75) is False
        persist_release.set()
        first.join(timeout=20)
        second.join(timeout=20)
    finally:
        persist_release.set()
        for process in (first, second):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert first.exitcode == 0
    assert second.exitcode == 0
    observed = sorted(results.get(timeout=5) for _ in range(2))
    assert observed == [
        ("250", True, ""),
        ("300", False, "save_failed"),
    ]
    checkpoint_path = (
        tmp_path / "runtime" / "discord_save" / "collection" / "200__start.json"
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["cutoff_message_id"] == "300"
