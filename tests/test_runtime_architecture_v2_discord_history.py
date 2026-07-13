from __future__ import annotations

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
    ParticipantResolver,
    load_bot_identities,
)
from src.runtime_architecture_v2.discord_history import (
    DiscordHistoryClient,
    DiscordHistoryError,
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
        if "before" not in query:
            return [_message(str(i)) for i in range(200, 100, -1)]
        if query["before"] == "101":
            return [_message(str(i)) for i in range(100, 0, -1)]
        return []

    result = DiscordHistoryClient(
        token="secret", request_json=request
    ).fetch_conversation("200")

    assert len(result.messages) == 200
    assert result.messages[0].message_id == "1"
    assert result.messages[-1].message_id == "200"
    assert calls[2][2] == {"limit": "100", "before": "101"}


@pytest.mark.parametrize("thread_type", [10, 11, 12])
def test_fetch_conversation_accepts_each_guild_thread_type(thread_type):
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, path, _query: _thread(thread_type)
        if path == "/channels/200"
        else [],
    )

    assert client.fetch_conversation("200").thread_id == "200"


def test_fetch_conversation_rejects_non_thread_guild_channel():
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda *_: {"id": "100", "type": 0, "name": "general"},
    )

    with pytest.raises(DiscordHistoryError, match="thread_required"):
        client.fetch_conversation("100")


def test_fetch_conversation_rejects_malformed_message_page():
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, path, _query: _thread()
        if path == "/channels/200"
        else {"message": "not a list"},
    )

    with pytest.raises(DiscordHistoryError, match="invalid_message_page"):
        client.fetch_conversation("200")


def test_fetch_conversation_preserves_empty_thread_metadata():
    result = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, path, _query: _thread(12)
        if path == "/channels/200"
        else [],
    ).fetch_conversation("200")

    assert result == DiscordConversation(
        guild_id="1",
        parent_channel_id="100",
        thread_id="200",
        thread_name="idea",
        visibility="private_thread",
        messages=(),
    )


def test_fetch_conversation_preserves_attachment_metadata_and_urls():
    attachment = {
        "id": "attachment-1",
        "filename": "brief.pdf",
        "content_type": "application/pdf",
        "size": 42,
        "url": "https://cdn.discordapp.com/attachments/brief.pdf?signature=kept",
    }
    result = DiscordHistoryClient(
        token="secret",
        request_json=lambda _method, path, _query: _thread()
        if path == "/channels/200"
        else [_message("1", attachments=[attachment])],
    ).fetch_conversation("200")

    assert result.messages[0].attachments == (
        DiscordAttachment(
            attachment_id="attachment-1",
            filename="brief.pdf",
            content_type="application/pdf",
            size=42,
            url="https://cdn.discordapp.com/attachments/brief.pdf?signature=kept",
        ),
    )


def test_fetch_conversation_stops_at_configured_message_cap():
    calls = []

    def request(_method, path, query):
        calls.append((path, query))
        if path == "/channels/200":
            return _thread()
        return [_message(str(i)) for i in range(10, 0, -1)]

    result = DiscordHistoryClient(
        token="secret", request_json=request, max_messages=3
    ).fetch_conversation("200")

    assert [message.message_id for message in result.messages] == ["8", "9", "10"]
    assert calls == [
        ("/channels/200", {}),
        ("/channels/200/messages", {"limit": "100"}),
    ]


def test_transport_error_does_not_include_token(monkeypatch):
    client = DiscordHistoryClient(token="secret-token")

    def fail(*_args, **_kwargs):
        raise OSError("secret-token")

    monkeypatch.setattr(
        "src.runtime_architecture_v2.discord_history.urllib.request.urlopen", fail
    )

    with pytest.raises(DiscordHistoryError) as error:
        client.fetch_conversation("200")

    assert "secret-token" not in str(error.value)


def test_http_error_includes_only_sanitized_status(monkeypatch):
    client = DiscordHistoryClient(token="secret-token")

    def fail(request, *_args, **_kwargs):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "secret-token",
            {"Authorization": "secret-token"},
            None,
        )

    monkeypatch.setattr(
        "src.runtime_architecture_v2.discord_history.urllib.request.urlopen", fail
    )

    with pytest.raises(DiscordHistoryError) as error:
        client.fetch_conversation("200")

    assert str(error.value) == "discord_http_status_403"


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
        visibility="private_thread",
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
    for profile in PROFILE_ROLES:
        env_path = profile_root / profile / ".env"
        env_path.parent.mkdir(parents=True)
        env_path.write_text(
            "DISCORD_BOT_TOKEN=secret-token-for-test\nOTHER_SECRET=not-exported\n",
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
