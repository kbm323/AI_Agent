"""Bounded Discord API reader for invocation-scoped thread history."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.runtime_architecture_v2.discord_conversation import (
    DiscordAttachment,
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
)
from src.runtime_architecture_v2.knowledge import sanitize_knowledge_text

RequestJson = Callable[[str, str, Mapping[str, str]], object]
Sleep = Callable[[float], None]

_THREAD_VISIBILITY = {
    10: "announcement_thread",
    11: "public_thread",
    12: "private",
}
_DM_CHANNEL_TYPES = {1, 3}
_MAX_MESSAGES = 10_000
_SNOWFLAKE_RE = re.compile(r"^[0-9]{1,24}$")
_HTTP_STATUS_RE = re.compile(r"^discord_http_status_([0-9]{3})$")
_CHECKPOINT_VERSION = 1


class DiscordHistoryError(RuntimeError):
    """A sanitized failure while reading Discord history."""

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class DiscordHistoryClient:
    """Read one Discord guild thread using an in-memory bot token."""

    def __init__(
        self,
        *,
        token: str,
        request_json: RequestJson | None = None,
        max_messages: int = _MAX_MESSAGES,
        checkpoint_root: str | Path | None = None,
        max_retries: int = 2,
        max_retry_delay: float = 5.0,
        sleep: Sleep = time.sleep,
    ) -> None:
        if not token:
            raise DiscordHistoryError("missing_discord_token")
        if max_messages <= 0:
            raise DiscordHistoryError("invalid_max_messages")
        if max_messages > _MAX_MESSAGES:
            raise DiscordHistoryError("max_messages_exceeds_limit")
        if max_retries < 0:
            raise DiscordHistoryError("invalid_max_retries")
        if max_retry_delay < 0:
            raise DiscordHistoryError("invalid_max_retry_delay")
        self._token = token
        self._request_json = request_json or self._request
        self._max_messages = max_messages
        self._checkpoint_root = (
            Path(checkpoint_root) if checkpoint_root is not None else None
        )
        self._max_retries = max_retries
        self._max_retry_delay = max_retry_delay
        self._sleep = sleep

    def classify_source(self, source_id: str) -> str:
        """Return a reliable Discord channel kind for boundary decisions."""

        channel = self._fetch_channel(source_id)
        channel_type = _channel_type(channel)
        if channel_type in _THREAD_VISIBILITY:
            return "thread"
        if channel_type in _DM_CHANNEL_TYPES:
            return "dm"
        return "guild_channel"

    def fetch_conversation(
        self,
        source_id: str,
        *,
        cutoff_message_id: str,
        after_message_id: str | None = None,
        expected_kind: str = "thread",
    ) -> DiscordConversation:
        """Fetch messages older than the immutable invocation snowflake."""

        _validate_snowflake(source_id, "source_id")
        _validate_snowflake(cutoff_message_id, "cutoff_message_id")
        if after_message_id is not None:
            _validate_snowflake(after_message_id, "after_message_id")
            if int(after_message_id) >= int(cutoff_message_id):
                raise DiscordHistoryError("invalid_message_boundaries")
        if expected_kind not in {"thread", "dm"}:
            raise DiscordHistoryError("invalid_expected_kind")

        channel = self._fetch_channel(source_id)
        channel_type = _channel_type(channel)
        actual_kind = (
            "thread"
            if channel_type in _THREAD_VISIBILITY
            else "dm"
            if channel_type in _DM_CHANNEL_TYPES
            else "guild_channel"
        )
        if actual_kind != expected_kind:
            raise DiscordHistoryError("thread_required")

        messages = self._fetch_all_messages(
            source_id,
            cutoff_message_id,
            after_message_id=after_message_id,
        )
        return self._to_conversation(channel, channel_type, actual_kind, messages)

    def _fetch_channel(self, source_id: str) -> Mapping[str, Any]:
        _validate_snowflake(source_id, "source_id")
        channel = self._request_with_retry("GET", f"/channels/{source_id}", {})
        if not isinstance(channel, Mapping):
            raise DiscordHistoryError("invalid_channel_response")
        return channel

    def _fetch_all_messages(
        self,
        source_id: str,
        cutoff_message_id: str,
        *,
        after_message_id: str | None,
    ) -> tuple[DiscordMessage, ...]:
        checkpoint = self._load_collection_checkpoint(
            source_id,
            cutoff_message_id,
            after_message_id=after_message_id,
        )
        messages_by_id = {
            message.message_id: message for message in checkpoint["messages"]
        }
        before = checkpoint["before"]
        complete = checkpoint["complete"]

        while not complete and len(messages_by_id) < self._max_messages:
            query = {"limit": "100", "before": before}
            page = self._request_with_retry(
                "GET",
                f"/channels/{source_id}/messages",
                query,
            )
            if not isinstance(page, list):
                raise DiscordHistoryError("invalid_message_page")
            if not page:
                complete = True
                self._write_collection_checkpoint(
                    source_id,
                    cutoff_message_id,
                    after_message_id=after_message_id,
                    before=before,
                    complete=True,
                    messages=messages_by_id.values(),
                )
                break

            page_ids: list[str] = []
            crossed_after_boundary = False
            for raw_message in page:
                message = self._to_message(raw_message)
                page_ids.append(message.message_id)
                if int(message.message_id) >= int(cutoff_message_id):
                    continue
                if after_message_id is not None and int(message.message_id) <= int(
                    after_message_id
                ):
                    crossed_after_boundary = True
                    continue
                messages_by_id.setdefault(message.message_id, message)
                if len(messages_by_id) == self._max_messages:
                    break

            if not page_ids:
                raise DiscordHistoryError("invalid_message_page")
            next_before = min(page_ids, key=int)
            if int(next_before) >= int(before):
                raise DiscordHistoryError("invalid_pagination_cursor")
            before = next_before
            complete = (
                len(page) < 100
                or len(messages_by_id) == self._max_messages
                or crossed_after_boundary
            )
            self._write_collection_checkpoint(
                source_id,
                cutoff_message_id,
                after_message_id=after_message_id,
                before=before,
                complete=complete,
                messages=messages_by_id.values(),
            )

        return tuple(
            sorted(messages_by_id.values(), key=lambda message: int(message.message_id))
        )

    def _request_with_retry(
        self,
        method: str,
        path: str,
        query: Mapping[str, str],
    ) -> object:
        for retry_index in range(self._max_retries + 1):
            try:
                return self._request_json(method, path, query)
            except DiscordHistoryError as error:
                if retry_index == self._max_retries or not _is_retryable(error.code):
                    raise
                delay = _retry_delay(error, retry_index)
                self._sleep(min(delay, self._max_retry_delay))
        raise DiscordHistoryError("discord_transport_error")

    def _to_conversation(
        self,
        channel: Mapping[str, Any],
        channel_type: int,
        channel_kind: str,
        messages: tuple[DiscordMessage, ...],
    ) -> DiscordConversation:
        return DiscordConversation(
            guild_id=str(channel.get("guild_id", "")),
            parent_channel_id=str(channel.get("parent_id", "")),
            thread_id=str(channel.get("id", "")),
            thread_name=sanitize_knowledge_text(str(channel.get("name", ""))),
            visibility=(
                "private" if channel_kind == "dm" else _THREAD_VISIBILITY[channel_type]
            ),
            messages=messages,
            channel_kind=channel_kind,
        )

    def _to_message(self, raw_message: object) -> DiscordMessage:
        if not isinstance(raw_message, Mapping):
            raise DiscordHistoryError("invalid_message")
        message_id = str(raw_message.get("id", ""))
        _validate_snowflake(message_id, "message_id")
        author = raw_message.get("author")
        if not isinstance(author, Mapping):
            raise DiscordHistoryError("invalid_message_author")
        attachments = raw_message.get("attachments", [])
        if not isinstance(attachments, list):
            raise DiscordHistoryError("invalid_attachments")
        return DiscordMessage(
            message_id=message_id,
            created_at=sanitize_knowledge_text(str(raw_message.get("timestamp", ""))),
            content=sanitize_knowledge_text(str(raw_message.get("content", ""))),
            author=DiscordAuthor(
                user_id=str(author.get("id", "")),
                display_name=sanitize_knowledge_text(
                    str(author.get("global_name") or author.get("username", ""))
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
            filename=sanitize_knowledge_text(str(raw_attachment.get("filename", ""))),
            content_type=sanitize_knowledge_text(
                str(raw_attachment.get("content_type") or "")
            ),
            size=size,
            url=sanitize_knowledge_text(str(raw_attachment.get("url", ""))),
        )

    def _checkpoint_path(
        self,
        thread_id: str,
        cutoff_message_id: str,
    ) -> Path | None:
        if self._checkpoint_root is None:
            return None
        return self._checkpoint_root / f"{thread_id}__{cutoff_message_id}.json"

    def _load_collection_checkpoint(
        self,
        source_id: str,
        cutoff_message_id: str,
        *,
        after_message_id: str | None,
    ) -> dict[str, Any]:
        empty = {
            "before": cutoff_message_id,
            "complete": False,
            "messages": (),
        }
        path = self._checkpoint_path(source_id, cutoff_message_id)
        if path is None or not path.exists():
            return empty
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "version",
                "source_id",
                "cutoff_message_id",
                "after_message_id",
                "before",
                "complete",
                "messages",
            }:
                raise TypeError
            if (
                payload["version"] != _CHECKPOINT_VERSION
                or payload["source_id"] != source_id
                or payload["cutoff_message_id"] != cutoff_message_id
                or payload["after_message_id"] != after_message_id
                or not isinstance(payload["before"], str)
                or not isinstance(payload["complete"], bool)
                or not isinstance(payload["messages"], list)
            ):
                raise TypeError
            _validate_snowflake(payload["before"], "checkpoint_before")
            messages = tuple(self._to_message(item) for item in payload["messages"])
            if len(messages) > self._max_messages:
                raise ValueError
            message_ids = [message.message_id for message in messages]
            if len(message_ids) != len(set(message_ids)) or any(
                int(message_id) >= int(cutoff_message_id)
                or (
                    after_message_id is not None
                    and int(message_id) <= int(after_message_id)
                )
                for message_id in message_ids
            ):
                raise ValueError
            if messages and int(payload["before"]) > min(map(int, message_ids)):
                raise ValueError
            return {
                "before": payload["before"],
                "complete": payload["complete"],
                "messages": messages,
            }
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as error:
            raise DiscordHistoryError("invalid_collection_checkpoint") from error

    def _write_collection_checkpoint(
        self,
        source_id: str,
        cutoff_message_id: str,
        *,
        after_message_id: str | None,
        before: str,
        complete: bool,
        messages: Any,
    ) -> None:
        path = self._checkpoint_path(source_id, cutoff_message_id)
        if path is None:
            return
        ordered = sorted(messages, key=lambda message: int(message.message_id))
        payload = {
            "version": _CHECKPOINT_VERSION,
            "source_id": source_id,
            "cutoff_message_id": cutoff_message_id,
            "after_message_id": after_message_id,
            "before": before,
            "complete": complete,
            "messages": [_message_payload(message) for message in ordered],
        }
        _atomic_write_json(path, payload)

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
            retry_after = _http_retry_after(error) if error.code == 429 else None
            try:
                error.close()
            finally:
                raise DiscordHistoryError(
                    f"discord_http_status_{error.code}",
                    retry_after=retry_after,
                ) from None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise DiscordHistoryError("discord_transport_error") from None


def _channel_type(channel: Mapping[str, Any]) -> int:
    try:
        return int(channel.get("type", -1))
    except (TypeError, ValueError):
        raise DiscordHistoryError("invalid_channel_type") from None


def _validate_snowflake(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SNOWFLAKE_RE.fullmatch(value):
        raise DiscordHistoryError(f"invalid_{label}")


def _is_retryable(code: str) -> bool:
    if code == "discord_transport_error":
        return True
    match = _HTTP_STATUS_RE.fullmatch(code)
    if match is None:
        return False
    status = int(match.group(1))
    return status == 429 or 500 <= status <= 599


def _retry_delay(error: DiscordHistoryError, retry_index: int) -> float:
    if error.code == "discord_http_status_429" and error.retry_after is not None:
        return max(0.0, error.retry_after)
    return 0.25 * (2**retry_index)


def _http_retry_after(error: urllib.error.HTTPError) -> float | None:
    header_value = error.headers.get("Retry-After") if error.headers else None
    if header_value is not None:
        try:
            return max(0.0, float(header_value))
        except (TypeError, ValueError):
            pass
    try:
        body = json.loads(error.read(4096).decode("utf-8"))
        retry_after = body.get("retry_after") if isinstance(body, dict) else None
        return max(0.0, float(retry_after)) if retry_after is not None else None
    except (
        AttributeError,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        return None


def _message_payload(message: DiscordMessage) -> dict[str, object]:
    return {
        "id": message.message_id,
        "timestamp": message.created_at,
        "content": message.content,
        "author": {
            "id": message.author.user_id,
            "username": message.author.display_name,
            "bot": message.author.bot,
        },
        "attachments": [
            {
                "id": attachment.attachment_id,
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
                "url": attachment.url,
            }
            for attachment in message.attachments
        ],
    }


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
