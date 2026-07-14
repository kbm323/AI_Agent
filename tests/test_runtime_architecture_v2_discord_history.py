from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from scripts import sync_discord_bot_identities as identity_sync
from scripts.sync_discord_bot_identities import PROFILE_ROLES, sync_bot_identities
from src.runtime_architecture_v2.discord_conversation import (
    DiscordAttachment,
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
    DiscordSourceIdentity,
    ParticipantResolver,
    load_bot_identities,
)
from src.runtime_architecture_v2.discord_history import (
    DiscordHistoryClient,
    DiscordHistoryError,
)

_CUTOFF = "999"


def _fetch(
    client: DiscordHistoryClient,
    source_id: str = "200",
    cutoff_message_id: str = _CUTOFF,
) -> DiscordConversation:
    return client.fetch_conversation(
        source_id,
        cutoff_message_id=cutoff_message_id,
    )


def _message(message_id: str, *, attachments=None):
    return {
        "id": message_id,
        "timestamp": "2026-07-13T00:00:00.000000+00:00",
        "content": f"message {message_id}",
        "author": {"id": "42", "username": "KBM", "bot": False},
        "attachments": attachments or [],
    }


def _thread(thread_type: int = 11):
    return {
        "id": "200",
        "type": thread_type,
        "name": "idea",
        "parent_id": "100",
        "guild_id": "1",
    }


def test_fetch_conversation_paginates_deduplicates_and_sorts_oldest_first():
    calls = []

    def request(method, path, query):
        calls.append((method, path, query))
        if path == "/channels/200":
            return _thread()
        if query.get("before") == _CUTOFF:
            return [_message(str(i)) for i in range(200, 100, -1)]
        if query["before"] == "101":
            return [_message(str(i)) for i in range(100, 0, -1)]
        return []

    result = _fetch(DiscordHistoryClient(token="secret", request_json=request))

    assert len(result.messages) == 200
    assert result.messages[0].message_id == "1"
    assert result.messages[-1].message_id == "200"
    assert calls[2][2] == {"limit": "100", "before": "101"}


@pytest.mark.parametrize("thread_type", [10, 11, 12])
def test_fetch_conversation_accepts_each_guild_thread_type(thread_type):
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, path, _query: (
            _thread(thread_type) if path == "/channels/200" else []
        ),
    )

    assert _fetch(client).thread_id == "200"


@pytest.mark.parametrize(
    ("channel_type", "expected"),
    [
        (1, "dm"),
        (0, "guild_channel"),
        (10, "thread"),
        (11, "thread"),
        (12, "thread"),
    ],
)
def test_classify_source_uses_discord_channel_type(channel_type, expected):
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, _path, _query: {
            "id": "200",
            "type": channel_type,
        },
    )

    assert client.classify_source("200") == expected


def test_fetch_private_dm_honors_session_start_and_invocation_boundaries():
    calls = []

    def request(_method, path, query):
        calls.append((path, query))
        if path == "/channels/900":
            return {"id": "900", "type": 1, "name": ""}
        return [_message("951"), _message("900"), _message("800")]

    result = DiscordHistoryClient(
        token="secret", request_json=request
    ).fetch_conversation(
        "900",
        cutoff_message_id="950",
        after_message_id="800",
        expected_kind="dm",
    )

    assert result.visibility == "private"
    assert result.channel_kind == "dm"
    assert [message.message_id for message in result.messages] == ["900"]
    assert calls[1] == (
        "/channels/900/messages",
        {"limit": "100", "before": "950"},
    )


def test_dm_stops_and_resumes_when_page_crosses_session_boundary(tmp_path):
    checkpoint_root = tmp_path / "collection"
    message_calls = 0

    def request(_method, path, query):
        nonlocal message_calls
        if path == "/channels/900":
            return {"id": "900", "type": 1, "name": ""}
        message_calls += 1
        if query["before"] == "950":
            return [_message(str(i)) for i in range(949, 849, -1)]
        if query["before"] == "850":
            return [_message(str(i)) for i in range(849, 749, -1)]
        raise AssertionError("history older than the DM session was requested")

    first = DiscordHistoryClient(
        token="secret",
        request_json=request,
        checkpoint_root=checkpoint_root,
    ).fetch_conversation(
        "900",
        cutoff_message_id="950",
        after_message_id="800",
        expected_kind="dm",
    )

    assert message_calls == 2
    assert first.messages[0].message_id == "801"
    assert first.messages[-1].message_id == "949"

    resumed_queries = []

    def resumed_request(_method, path, query):
        if path == "/channels/900":
            return {"id": "900", "type": 1, "name": ""}
        resumed_queries.append(query)
        return [_message(str(i)) for i in range(999, 899, -1)]

    resumed = DiscordHistoryClient(
        token="secret",
        request_json=resumed_request,
        checkpoint_root=checkpoint_root,
    ).fetch_conversation(
        "900",
        cutoff_message_id="1000",
        after_message_id="800",
        expected_kind="dm",
    )

    assert resumed_queries == [{"limit": "100", "before": "1000"}]
    assert resumed.messages[0].message_id == "801"
    assert resumed.messages[-1].message_id == "999"
    assert all(int(message.message_id) > 800 for message in resumed.messages)


def test_retry_budget_is_bounded_on_late_page_failure():
    attempts = 0

    def request(_method, path, _query):
        nonlocal attempts
        if path == "/channels/200":
            return _thread()
        attempts += 1
        raise DiscordHistoryError("discord_http_status_503")

    with pytest.raises(DiscordHistoryError, match="discord_http_status_503"):
        _fetch(
            DiscordHistoryClient(
                token="secret",
                request_json=request,
                sleep=lambda _delay: None,
                max_retries=2,
            )
        )

    assert attempts == 3


def test_fetch_conversation_rejects_non_thread_guild_channel():
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda *_: {"id": "100", "type": 0, "name": "general"},
    )

    with pytest.raises(DiscordHistoryError, match="thread_required"):
        _fetch(client, "100")


def test_fetch_conversation_rejects_malformed_message_page():
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, path, _query: (
            _thread() if path == "/channels/200" else {"message": "not a list"}
        ),
    )

    with pytest.raises(DiscordHistoryError, match="invalid_message_page"):
        _fetch(client)


def test_fetch_conversation_preserves_empty_thread_metadata():
    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=lambda _method, path, _query: (
                _thread(12) if path == "/channels/200" else []
            ),
        )
    )

    assert result == DiscordConversation(
        guild_id="1",
        parent_channel_id="100",
        thread_id="200",
        thread_name="idea",
        visibility="private",
        messages=(),
        source_identity=DiscordSourceIdentity.guild_thread("200", "1", "100"),
    )


def test_fetch_conversation_preserves_attachment_metadata_and_urls():
    attachment = {
        "id": "attachment-1",
        "filename": "brief.pdf",
        "content_type": "application/pdf",
        "size": 42,
        "url": "https://cdn.discordapp.com/attachments/brief.pdf?signature=kept",
    }
    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=lambda _method, path, _query: (
                _thread()
                if path == "/channels/200"
                else [_message("1", attachments=[attachment])]
            ),
        )
    )

    assert result.messages[0].attachments == (
        DiscordAttachment(
            attachment_id="attachment-1",
            filename="brief.pdf",
            content_type="application/pdf",
            size=42,
            url=(
                "https://cdn.discordapp.com/attachments/brief.pdf?"
                "signature=[REDACTED_SECRET]"
            ),
        ),
    )


def test_fetch_conversation_stops_at_configured_message_cap():
    calls = []

    def request(_method, path, query):
        calls.append((path, query))
        if path == "/channels/200":
            return _thread()
        return [_message(str(i)) for i in range(10, 0, -1)]

    result = _fetch(
        DiscordHistoryClient(token="secret", request_json=request, max_messages=3)
    )

    assert [message.message_id for message in result.messages] == ["8", "9", "10"]
    assert calls == [
        ("/channels/200", {}),
        ("/channels/200/messages", {"limit": "100", "before": _CUTOFF}),
    ]


def test_client_rejects_message_caps_above_absolute_limit():
    with pytest.raises(DiscordHistoryError, match="max_messages_exceeds_limit"):
        DiscordHistoryClient(token="secret", max_messages=10_001)


def test_transport_error_does_not_include_token(monkeypatch):
    client = DiscordHistoryClient(token="secret-token")

    def fail(*_args, **_kwargs):
        raise OSError("secret-token")

    monkeypatch.setattr(
        "src.runtime_architecture_v2.discord_history.urllib.request.urlopen", fail
    )

    with pytest.raises(DiscordHistoryError) as error:
        _fetch(client)

    assert "secret-token" not in str(error.value)


def test_http_error_includes_only_sanitized_status(monkeypatch):
    client = DiscordHistoryClient(token="secret-token")
    body = io.BytesIO(b"secret-token response body")

    def fail(request, *_args, **_kwargs):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "secret-token",
            {"Authorization": "secret-token"},
            body,
        )

    monkeypatch.setattr(
        "src.runtime_architecture_v2.discord_history.urllib.request.urlopen", fail
    )

    with pytest.raises(DiscordHistoryError) as error:
        _fetch(client)

    assert str(error.value) == "discord_http_status_403"
    assert body.closed is True


def test_invocation_cutoff_is_exclusive_even_if_api_returns_later_messages():
    calls = []

    def request(_method, path, query):
        calls.append((path, query))
        if path == "/channels/200":
            return _thread()
        return [_message("251"), _message("249"), _message("248")]

    result = _fetch(
        DiscordHistoryClient(token="secret", request_json=request),
        cutoff_message_id="250",
    )

    assert [message.message_id for message in result.messages] == ["248", "249"]
    assert calls[1] == (
        "/channels/200/messages",
        {"limit": "100", "before": "250"},
    )


def test_timestamp_floor_excludes_later_same_millisecond_reversed_snowflake():
    timestamp_floor = (123456789012345678 >> 22) << 22
    raw_interaction_id = timestamp_floor + ((1 << 22) - 1)
    later_message_id = timestamp_floor + 1
    assert later_message_id < raw_interaction_id

    calls = []

    def request(_method, path, query):
        calls.append((path, query))
        if path == "/channels/200":
            return _thread()
        return [_message(str(later_message_id)), _message(str(timestamp_floor - 1))]

    result = _fetch(
        DiscordHistoryClient(token="secret", request_json=request),
        cutoff_message_id=str(timestamp_floor),
    )

    assert [message.message_id for message in result.messages] == [
        str(timestamp_floor - 1)
    ]
    assert calls[1][1]["before"] == str(timestamp_floor)


def test_retries_429_with_bounded_retry_after():
    attempts = 0
    sleeps = []

    def request(_method, path, _query):
        nonlocal attempts
        if path == "/channels/200":
            return _thread()
        attempts += 1
        if attempts == 1:
            raise DiscordHistoryError(
                "discord_http_status_429",
                retry_after=9.0,
            )
        return []

    _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=request,
            sleep=sleeps.append,
            max_retry_delay=2.0,
        )
    )

    assert attempts == 2
    assert sleeps == [2.0]


def test_retries_transient_5xx_with_bounded_backoff():
    attempts = 0
    sleeps = []

    def request(_method, path, _query):
        nonlocal attempts
        if path == "/channels/200":
            return _thread()
        attempts += 1
        if attempts == 1:
            raise DiscordHistoryError("discord_http_status_503")
        return []

    _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=request,
            sleep=sleeps.append,
        )
    )

    assert attempts == 2
    assert sleeps == [0.25]


def test_later_cutoff_adopts_paginated_progress_without_duplicates_or_secrets(
    tmp_path,
):
    checkpoint_root = tmp_path / "collection"
    first_page = [_message(str(i)) for i in range(200, 100, -1)]
    first_page[0]["content"] = "https://alice:p%40ss@example.test/private"

    def first_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        if query["before"] == "250":
            return first_page
        raise DiscordHistoryError("discord_http_status_503")

    first_client = DiscordHistoryClient(
        token="secret",
        request_json=first_request,
        checkpoint_root=checkpoint_root,
        max_retries=0,
    )
    with pytest.raises(DiscordHistoryError, match="discord_http_status_503"):
        _fetch(first_client, cutoff_message_id="250")

    checkpoint_paths = list(checkpoint_root.glob("*.json"))
    assert len(checkpoint_paths) == 1
    checkpoint_text = checkpoint_paths[0].read_text(encoding="utf-8")
    assert "alice" not in checkpoint_text
    assert "p%40ss" not in checkpoint_text
    assert "https://example.test/private" in checkpoint_text

    resumed_queries = []

    def resumed_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        resumed_queries.append(query)
        if query["before"] == "300":
            return [_message(str(i)) for i in range(299, 199, -1)]
        if query["before"] == "101":
            return [_message("100"), _message("99")]
        raise AssertionError(f"unexpected query: {query}")

    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=resumed_request,
            checkpoint_root=checkpoint_root,
        ),
        cutoff_message_id="300",
    )

    assert resumed_queries == [
        {"limit": "100", "before": "300"},
        {"limit": "100", "before": "101"},
    ]
    assert len(result.messages) == 201
    assert len({message.message_id for message in result.messages}) == 201
    assert result.messages[0].message_id == "99"
    assert result.messages[-1].message_id == "299"
    assert len(list(checkpoint_root.glob("*.json"))) == 1


def test_failed_page_checkpoint_redacts_quoted_and_credential_assignments(tmp_path):
    checkpoint_root = tmp_path / "collection"
    first_page = [_message(str(i)) for i in range(249, 149, -1)]
    first_page[0]["content"] = (
        'Keep {"password":"CHECKPOINT_PASSWORD","name":"Oracle"} '
        'credential="CHECKPOINT_CREDENTIAL" auth=CHECKPOINT_AUTH'
    )

    def request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        if query["before"] == "250":
            return first_page
        raise DiscordHistoryError("discord_http_status_503")

    with pytest.raises(DiscordHistoryError, match="discord_http_status_503"):
        _fetch(
            DiscordHistoryClient(
                token="secret",
                request_json=request,
                checkpoint_root=checkpoint_root,
                max_retries=0,
            ),
            cutoff_message_id="250",
        )

    checkpoint = json.loads(
        next(checkpoint_root.glob("*.json")).read_text(encoding="utf-8")
    )
    assert checkpoint["messages"][-1]["content"] == (
        'Keep {[REDACTED_SECRET],"name":"Oracle"} [REDACTED_SECRET] [REDACTED_SECRET]'
    )


def test_later_cutoff_migrates_first_wave_cutoff_named_checkpoint(tmp_path):
    checkpoint_root = tmp_path / "collection"
    checkpoint_root.mkdir()
    legacy_path = checkpoint_root / "200__250.json"
    legacy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source_id": "200",
                "cutoff_message_id": "250",
                "after_message_id": None,
                "before": "150",
                "complete": False,
                "messages": [_message(str(i)) for i in range(249, 149, -1)],
            }
        ),
        encoding="utf-8",
    )
    queries = []

    def request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        queries.append(query)
        if query["before"] == "300":
            return [_message(str(i)) for i in range(299, 199, -1)]
        if query["before"] == "150":
            return [_message("149")]
        raise AssertionError(f"unexpected query: {query}")

    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=request,
            checkpoint_root=checkpoint_root,
        ),
        cutoff_message_id="300",
    )

    assert queries == [
        {"limit": "100", "before": "300"},
        {"limit": "100", "before": "150"},
    ]
    assert result.messages[0].message_id == "149"
    assert result.messages[-1].message_id == "299"
    assert legacy_path.exists() is False
    assert (checkpoint_root / "200__start.json").is_file()


def test_near_full_checkpoint_fetches_entire_newer_interval_before_cap(tmp_path):
    checkpoint_root = tmp_path / "collection"
    checkpoint_root.mkdir()
    (checkpoint_root / "200__start.json").write_text(
        json.dumps(
            {
                "version": 2,
                "source_id": "200",
                "cutoff_message_id": "10000",
                "after_message_id": None,
                "before": "50",
                "complete": True,
                "messages": [_message(str(i)) for i in range(50, 10000)],
            }
        ),
        encoding="utf-8",
    )
    queries = []

    def request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        queries.append(query)
        before = int(query["before"])
        if before > 10000:
            return [_message(str(i)) for i in range(before - 1, before - 101, -1)]
        raise AssertionError("collector crossed the adopted cutoff")

    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=request,
            checkpoint_root=checkpoint_root,
        ),
        cutoff_message_id="10300",
    )

    assert [query["before"] for query in queries] == ["10300", "10200", "10100"]
    ids = [int(message.message_id) for message in result.messages]
    assert len(ids) == 10_000
    assert ids == list(range(300, 10300))


def test_full_checkpoint_is_discarded_after_newer_interval_reaches_cap(tmp_path):
    checkpoint_root = tmp_path / "collection"
    checkpoint_root.mkdir()
    (checkpoint_root / "200__start.json").write_text(
        json.dumps(
            {
                "version": 2,
                "source_id": "200",
                "cutoff_message_id": "10001",
                "after_message_id": None,
                "before": "1",
                "complete": True,
                "messages": [_message(str(i)) for i in range(1, 10001)],
            }
        ),
        encoding="utf-8",
    )
    queries = []

    def request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        queries.append(query)
        before = int(query["before"])
        if before > 10001:
            return [_message(str(i)) for i in range(before - 1, before - 101, -1)]
        raise AssertionError("inherited history must not be fetched")

    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=request,
            checkpoint_root=checkpoint_root,
        ),
        cutoff_message_id="20001",
    )

    assert len(queries) == 100
    assert queries[0]["before"] == "20001"
    assert queries[-1]["before"] == "10101"
    ids = [int(message.message_id) for message in result.messages]
    assert ids == list(range(10001, 20001))


def test_same_cutoff_resumes_partial_near_cap_adoption_without_middle_gap(tmp_path):
    checkpoint_root = tmp_path / "collection"
    checkpoint_root.mkdir()
    (checkpoint_root / "200__start.json").write_text(
        json.dumps(
            {
                "version": 2,
                "source_id": "200",
                "cutoff_message_id": "1000",
                "after_message_id": None,
                "before": "50",
                "complete": True,
                "messages": [_message(str(i)) for i in range(50, 1000)],
            }
        ),
        encoding="utf-8",
    )

    def failing_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        if query["before"] == "1200":
            return [_message(str(i)) for i in range(1199, 1099, -1)]
        raise DiscordHistoryError("discord_http_status_503")

    with pytest.raises(DiscordHistoryError, match="discord_http_status_503"):
        _fetch(
            DiscordHistoryClient(
                token="secret",
                request_json=failing_request,
                checkpoint_root=checkpoint_root,
                max_messages=1000,
                max_retries=0,
            ),
            cutoff_message_id="1200",
        )

    checkpoint = json.loads(
        (checkpoint_root / "200__start.json").read_text(encoding="utf-8")
    )
    assert checkpoint["version"] == 3
    assert checkpoint["before"] == "1100"
    assert checkpoint["adopted_cutoff_message_id"] == "1000"

    resumed_queries = []

    def resumed_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        resumed_queries.append(query["before"])
        if query["before"] == "1100":
            return [_message(str(i)) for i in range(1099, 999, -1)]
        raise AssertionError(f"unexpected query: {query}")

    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=resumed_request,
            checkpoint_root=checkpoint_root,
            max_messages=1000,
        ),
        cutoff_message_id="1200",
    )

    assert resumed_queries == ["1100"]
    assert [int(message.message_id) for message in result.messages] == list(
        range(200, 1200)
    )


def test_later_cutoff_finishes_partial_full_cap_adoption_without_middle_gap(tmp_path):
    checkpoint_root = tmp_path / "collection"
    checkpoint_root.mkdir()
    (checkpoint_root / "200__start.json").write_text(
        json.dumps(
            {
                "version": 2,
                "source_id": "200",
                "cutoff_message_id": "1001",
                "after_message_id": None,
                "before": "1",
                "complete": True,
                "messages": [_message(str(i)) for i in range(1, 1001)],
            }
        ),
        encoding="utf-8",
    )

    def failing_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        if query["before"] == "1201":
            return [_message(str(i)) for i in range(1200, 1100, -1)]
        raise DiscordHistoryError("discord_http_status_503")

    with pytest.raises(DiscordHistoryError, match="discord_http_status_503"):
        _fetch(
            DiscordHistoryClient(
                token="secret",
                request_json=failing_request,
                checkpoint_root=checkpoint_root,
                max_messages=1000,
                max_retries=0,
            ),
            cutoff_message_id="1201",
        )

    later_queries = []

    def later_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        later_queries.append(query["before"])
        if query["before"] == "1101":
            return [_message(str(i)) for i in range(1100, 1000, -1)]
        if query["before"] == "1401":
            return [_message(str(i)) for i in range(1400, 1300, -1)]
        raise DiscordHistoryError("discord_http_status_503")

    with pytest.raises(DiscordHistoryError, match="discord_http_status_503"):
        _fetch(
            DiscordHistoryClient(
                token="secret",
                request_json=later_request,
                checkpoint_root=checkpoint_root,
                max_messages=1000,
                max_retries=0,
            ),
            cutoff_message_id="1401",
        )

    assert later_queries == ["1101", "1401", "1301"]

    restarted_queries = []

    def restarted_request(_method, path, query):
        if path == "/channels/200":
            return _thread()
        restarted_queries.append(query["before"])
        if query["before"] == "1301":
            return [_message(str(i)) for i in range(1300, 1200, -1)]
        raise AssertionError(f"unexpected query: {query}")

    result = _fetch(
        DiscordHistoryClient(
            token="secret",
            request_json=restarted_request,
            checkpoint_root=checkpoint_root,
            max_messages=1000,
        ),
        cutoff_message_id="1401",
    )

    assert restarted_queries == ["1301"]
    assert [int(message.message_id) for message in result.messages] == list(
        range(401, 1401)
    )


def test_participant_resolver_uses_discord_id_before_display_name(tmp_path):
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "123": {
                    "role": "콘텐츠팀장",
                    "hermes_profile": "aicompanycontent",
                }
            }
        ),
        encoding="utf-8",
    )
    resolver = ParticipantResolver(load_bot_identities(path))

    resolved = resolver.resolve(
        DiscordAuthor(user_id="123", display_name="다른표시이름", bot=True)
    )

    assert resolved.role == "콘텐츠팀장"
    assert resolved.hermes_profile == "aicompanycontent"
    assert resolved.discord_name == "다른표시이름"
    assert resolved.discord_user_id == "123"


def test_unknown_human_keeps_display_name_without_company_role():
    resolved = ParticipantResolver({}).resolve(
        DiscordAuthor(user_id="999", display_name="KBM", bot=False)
    )

    assert resolved.role == ""
    assert resolved.hermes_profile == ""
    assert resolved.discord_name == "KBM"
    assert resolved.discord_user_id == "999"


def test_conversation_models_are_frozen_transport_data():
    attachment = DiscordAttachment(
        attachment_id="attachment-1",
        filename="brief.pdf",
        content_type="application/pdf",
        size=42,
        url="https://cdn.example.test/brief.pdf",
    )
    message = DiscordMessage(
        message_id="message-1",
        created_at="2026-07-13T00:00:00Z",
        content="Review this.",
        author=DiscordAuthor(user_id="999", display_name="KBM"),
        attachments=(attachment,),
    )
    conversation = DiscordConversation(
        guild_id="guild-1",
        parent_channel_id="channel-1",
        thread_id="thread-1",
        thread_name="Review",
        visibility="private",
        messages=(message,),
    )

    assert conversation.messages[0].attachments == (attachment,)


def test_sync_bot_identities_writes_only_non_secret_identity_data(tmp_path):
    assert PROFILE_ROLES == {
        "aicompanyassistant": "비서",
        "aicompanyceo": "대표",
        "aicompanycontent": "콘텐츠팀장",
        "aicompanyart": "아트팀장",
        "aicompanytech": "기술팀장",
        "aicompanymarketing": "마케팅팀장",
        "aicompanyquality": "품질관리팀장",
    }
    profile_root = tmp_path / "profiles"
    token_key = "_".join(("DISCORD", "BOT", "TOKEN"))
    for profile in PROFILE_ROLES:
        env_path = profile_root / profile / ".env"
        env_path.parent.mkdir(parents=True)
        env_path.write_text(
            f"{token_key}=secret-token-for-test\nOTHER_SECRET=not-exported\n",
            encoding="utf-8",
        )

    requests: list[tuple[str, dict[str, str]]] = []

    def fake_http_get(url: str, *, headers: dict[str, str]) -> dict[str, str]:
        requests.append((url, headers))
        return {"id": str(1000 + len(requests))}

    output_path = tmp_path / "runtime" / "discord_bot_identities.json"
    status = sync_bot_identities(
        output_path=output_path,
        profile_root=profile_root,
        http_get=fake_http_get,
    )

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert status == {"ok": True, "identity_count": 7, "path": str(output_path)}
    assert [url for url, _headers in requests] == [
        "https://discord.com/api/v10/users/@me"
    ] * 7
    assert written == {
        str(1001 + index): {"role": role, "hermes_profile": profile}
        for index, (profile, role) in enumerate(PROFILE_ROLES.items())
    }
    assert set(written["1001"]) == {"role", "hermes_profile"}


@pytest.mark.parametrize(
    ("dotenv_line", "expected_token"),
    [
        (" export DISCORD_BOT_TOKEN = plain-token # rotation note", "plain-token"),
        ('DISCORD_BOT_TOKEN = "quoted # token" # rotation note', "quoted # token"),
        ("DISCORD_BOT_TOKEN = 'single quoted # token'", "single quoted # token"),
    ],
)
def test_load_discord_bot_token_supports_ordinary_dotenv_forms(
    tmp_path, dotenv_line, expected_token
):
    env_path = tmp_path / ".env"
    env_path.write_text(f"{dotenv_line}\n", encoding="utf-8")

    assert identity_sync._load_discord_bot_token(env_path) == expected_token


def test_missing_token_error_does_not_include_env_contents(tmp_path):
    profile_root = tmp_path / "profiles"
    env_path = profile_root / "aicompanyassistant" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OTHER_SECRET=must-not-leak\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing Discord bot token") as error:
        sync_bot_identities(
            output_path=tmp_path / "identities.json",
            profile_root=profile_root,
            http_get=lambda *_args, **_kwargs: {"id": "unused"},
        )

    assert "must-not-leak" not in str(error.value)


def test_atomic_write_removes_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    destination = tmp_path / "discord_bot_identities.json"
    destination.write_text('{"existing":"identity"}\n', encoding="utf-8")
    temporary_paths: list[Path] = []

    def fail_replace(source, _destination):
        temporary_paths.append(Path(source))
        raise OSError("replace failed")

    monkeypatch.setattr(identity_sync.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        identity_sync._atomic_write_json(destination, {"new": "identity"})

    assert destination.read_text(encoding="utf-8") == '{"existing":"identity"}\n'
    assert temporary_paths
    assert not temporary_paths[0].exists()
