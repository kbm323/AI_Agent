"""Bounded Discord API reader for complete guild thread history."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from src.runtime_architecture_v2.discord_conversation import (
    DiscordAttachment,
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
)

RequestJson = Callable[[str, str, Mapping[str, str]], object]

_THREAD_VISIBILITY = {
    10: "announcement_thread",
    11: "public_thread",
    12: "private_thread",
}
_MAX_MESSAGES = 10_000


class DiscordHistoryError(RuntimeError):
    """A sanitized failure while reading Discord thread history."""


class DiscordHistoryClient:
    """Read one Discord guild thread using an in-memory bot token."""

    def __init__(
        self,
        *,
        token: str,
        request_json: RequestJson | None = None,
        max_messages: int = _MAX_MESSAGES,
    ) -> None:
        if not token:
            raise DiscordHistoryError("missing_discord_token")
        if max_messages <= 0:
            raise DiscordHistoryError("invalid_max_messages")
        if max_messages > _MAX_MESSAGES:
            raise DiscordHistoryError("max_messages_exceeds_limit")
        self._token = token
        self._request_json = request_json or self._request
        self._max_messages = max_messages

    def fetch_conversation(self, thread_id: str) -> DiscordConversation:
        """Fetch a thread and its messages, ordered from oldest to newest."""

        channel = self._request_json("GET", f"/channels/{thread_id}", {})
        if not isinstance(channel, Mapping):
            raise DiscordHistoryError("invalid_channel_response")
        try:
            channel_type = int(channel.get("type", -1))
        except (TypeError, ValueError):
            raise DiscordHistoryError("thread_required") from None
        if channel_type not in _THREAD_VISIBILITY:
            raise DiscordHistoryError("thread_required")

        messages = self._fetch_all_messages(thread_id)
        return self._to_conversation(channel, channel_type, messages)

    def _fetch_all_messages(self, thread_id: str) -> tuple[DiscordMessage, ...]:
        messages_by_id: dict[str, DiscordMessage] = {}
        before: str | None = None

        while len(messages_by_id) < self._max_messages:
            query = {"limit": "100"}
            if before is not None:
                query["before"] = before
            page = self._request_json("GET", f"/channels/{thread_id}/messages", query)
            if not isinstance(page, list):
                raise DiscordHistoryError("invalid_message_page")
            if not page:
                break

            page_ids: list[str] = []
            for raw_message in page:
                message = self._to_message(raw_message)
                page_ids.append(message.message_id)
                messages_by_id.setdefault(message.message_id, message)
                if len(messages_by_id) == self._max_messages:
                    break

            if len(page) < 100 or len(messages_by_id) == self._max_messages:
                break
            oldest_id = min(page_ids, key=int)
            if oldest_id == before:
                break
            before = oldest_id

        return tuple(
            sorted(messages_by_id.values(), key=lambda message: int(message.message_id))
        )

    def _to_conversation(
        self,
        channel: Mapping[str, Any],
        channel_type: int,
        messages: tuple[DiscordMessage, ...],
    ) -> DiscordConversation:
        return DiscordConversation(
            guild_id=str(channel.get("guild_id", "")),
            parent_channel_id=str(channel.get("parent_id", "")),
            thread_id=str(channel.get("id", "")),
            thread_name=str(channel.get("name", "")),
            visibility=_THREAD_VISIBILITY[channel_type],
            messages=messages,
        )

    def _to_message(self, raw_message: object) -> DiscordMessage:
        if not isinstance(raw_message, Mapping):
            raise DiscordHistoryError("invalid_message")
        message_id = str(raw_message.get("id", ""))
        try:
            int(message_id)
        except ValueError:
            raise DiscordHistoryError("invalid_message_id") from None
        author = raw_message.get("author")
        if not isinstance(author, Mapping):
            raise DiscordHistoryError("invalid_message_author")
        attachments = raw_message.get("attachments", [])
        if not isinstance(attachments, list):
            raise DiscordHistoryError("invalid_attachments")
        return DiscordMessage(
            message_id=message_id,
            created_at=str(raw_message.get("timestamp", "")),
            content=str(raw_message.get("content", "")),
            author=DiscordAuthor(
                user_id=str(author.get("id", "")),
                display_name=str(
                    author.get("global_name") or author.get("username", "")
                ),
                bot=bool(author.get("bot", False)),
            ),
            attachments=tuple(
                self._to_attachment(attachment) for attachment in attachments
            ),
        )

    def _to_attachment(self, raw_attachment: object) -> DiscordAttachment:
        if not isinstance(raw_attachment, Mapping):
            raise DiscordHistoryError("invalid_attachment")
        try:
            size = int(raw_attachment.get("size", 0))
        except (TypeError, ValueError):
            raise DiscordHistoryError("invalid_attachment") from None
        return DiscordAttachment(
            attachment_id=str(raw_attachment.get("id", "")),
            filename=str(raw_attachment.get("filename", "")),
            content_type=str(raw_attachment.get("content_type") or ""),
            size=size,
            url=str(raw_attachment.get("url", "")),
        )

    def _request(self, method: str, path: str, query: Mapping[str, str]) -> object:
        url = f"https://discord.com/api/v10{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bot {self._token}",
                "User-Agent": "AI_Agent/discord-save",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                error.close()
            finally:
                raise DiscordHistoryError(f"discord_http_status_{error.code}") from None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise DiscordHistoryError("discord_transport_error") from None
