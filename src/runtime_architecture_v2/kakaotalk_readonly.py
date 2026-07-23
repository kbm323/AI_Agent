"""Pure validation and selection rules for on-demand KakaoTalk collection."""

from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ReadOnlyBoundaryError(ValueError):
    """Raised when a collection request crosses the read-only boundary."""


@dataclass(frozen=True)
class ChatRoom:
    name: str
    last_activity: datetime


@dataclass(frozen=True)
class CollectionRequest:
    request_id: str
    chat_name: str
    operation: str = "read"
    initial_baseline: str | None = None


@dataclass(frozen=True)
class ChatMessage:
    event_id: str
    chat_name: str
    cursor: str
    message: str
    sender_id: str = ""
    sent_at: str = ""
    attachment: str | None = None


@dataclass(frozen=True)
class RoomCandidate:
    chat_id: str
    name: str
    chat_type: str
    last_updated_at: int


@dataclass(frozen=True)
class CollectionResult:
    chat_id: str
    chat_name: str
    collected_count: int
    cursor: str
    initialized: bool = False


class IrisHttpTransport:
    """Loopback-only JSON transport that can call only Iris ``/query``."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path not in {"", "/"}
        ):
            raise ReadOnlyBoundaryError("Iris endpoint must be loopback HTTP")
        self._endpoint = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def __call__(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path != "/query":
            raise ReadOnlyBoundaryError("Iris transport permits only /query")
        request = Request(
            f"{self._endpoint}/query",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ReadOnlyBoundaryError("invalid Iris query response")
        return result


class IrisReadOnlyClient:
    """Restricted Iris client exposing database reads only."""

    def __init__(
        self,
        transport: Callable[[str, dict[str, Any]], dict[str, Any]],
        *,
        room_name_cache: dict[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._room_name_cache = room_name_cache or {}

    def recent_rooms(self, *, limit: int = 10) -> list[RoomCandidate]:
        bounded_limit = min(max(limit, 0), 10)
        if bounded_limit == 0:
            return []
        payload = {
            "query": (
                "SELECT id, type, last_updated_at, "
                "json_extract(private_meta, '$.name') AS room_name "
                "FROM chat_rooms ORDER BY last_updated_at DESC LIMIT ?"
            ),
            "bind": [str(bounded_limit)],
        }
        response = self._transport("/query", payload)
        rows = response.get("data", [])
        candidates = []
        for row in rows:
            chat_id = str(row.get("id", ""))
            name = row.get("room_name") or self._room_name_cache.get(chat_id)
            if not chat_id or not isinstance(name, str) or not name.strip():
                continue
            candidates.append(
                RoomCandidate(
                    chat_id=chat_id,
                    name=name.strip(),
                    chat_type=str(row.get("type", "")),
                    last_updated_at=int(row.get("last_updated_at", 0)),
                )
            )
        return candidates[:bounded_limit]

    def messages_after(
        self,
        *,
        chat_id: str,
        chat_name: str,
        cursor: str,
        limit: int = 500,
    ) -> list[ChatMessage]:
        if not chat_id.isdecimal() or not cursor.isdecimal():
            raise ReadOnlyBoundaryError("chat_id and cursor must be numeric")
        bounded_limit = min(max(limit, 1), 500)
        payload = {
            "query": (
                "SELECT _id, chat_id, user_id, message, created_at, attachment "
                "FROM chat_logs WHERE chat_id = ? AND _id > ? "
                "ORDER BY _id ASC LIMIT ?"
            ),
            "bind": [chat_id, cursor, str(bounded_limit)],
        }
        response = self._transport("/query", payload)
        return [
            ChatMessage(
                event_id=str(row["_id"]),
                chat_name=chat_name,
                cursor=str(row["_id"]),
                message=str(row.get("message", "")),
                sender_id=str(row.get("user_id", "")),
                sent_at=str(row.get("created_at", "")),
                attachment=(
                    None if row.get("attachment") is None else str(row["attachment"])
                ),
            )
            for row in response.get("data", [])
        ]

    def latest_cursor(self, chat_id: str) -> str:
        if not chat_id.isdecimal():
            raise ReadOnlyBoundaryError("chat_id must be numeric")
        response = self._transport(
            "/query",
            {
                "query": (
                    "SELECT COALESCE(MAX(_id), 0) AS cursor "
                    "FROM chat_logs WHERE chat_id = ?"
                ),
                "bind": [chat_id],
            },
        )
        rows = response.get("data", [])
        cursor = str(rows[0].get("cursor", "0")) if rows else "0"
        if not cursor.isdecimal():
            raise ReadOnlyBoundaryError("Iris returned an invalid cursor")
        return cursor


class KakaoObsidianRawStore:
    """Persist immutable KakaoTalk evidence under the existing chat-log layout."""

    def __init__(self, vault_root: Path) -> None:
        self._root = vault_root / "raw" / "chat-logs" / "kakaotalk"

    def persist(self, chat_id: str, messages: list[ChatMessage]) -> list[str]:
        if not chat_id.isdecimal():
            raise ReadOnlyBoundaryError("chat_id must be numeric")
        room_root = self._root / chat_id
        room_root.mkdir(parents=True, exist_ok=True)
        saved = []
        for message in messages:
            if not message.event_id.isdecimal():
                raise ReadOnlyBoundaryError("event_id must be numeric")
            destination = room_root / f"{message.event_id}.json"
            temporary = room_root / f".{message.event_id}.json.tmp"
            payload = {
                "source": "kakaotalk",
                "event_id": message.event_id,
                "chat_id": chat_id,
                "chat_name": message.chat_name,
                "sender_id": message.sender_id,
                "sent_at": message.sent_at,
                "message": message.message,
                "attachment": message.attachment,
            }
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
            saved.append(message.event_id)
        return saved


class CursorStore:
    """Small durable store whose cursor advances only after persistence succeeds."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_name: str) -> Path:
        if not chat_name.isdecimal():
            raise ReadOnlyBoundaryError("cursor room key must be numeric")
        return self.root / f"{chat_name}.json"

    def _state(self, chat_name: str) -> dict[str, object]:
        path = self._path(chat_name)
        if not path.exists():
            return {"cursor": None, "event_ids": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def cursor(self, chat_name: str) -> str | None:
        return self._state(chat_name).get("cursor")  # type: ignore[return-value]

    def initialize(self, chat_name: str, cursor: str) -> None:
        if not cursor.isdecimal():
            raise ReadOnlyBoundaryError("cursor must be numeric")
        if self.cursor(chat_name) is not None:
            return
        self._write_state(chat_name, {"cursor": cursor, "event_ids": []})

    def _write_state(self, chat_name: str, state: dict[str, object]) -> None:
        path = self._path(chat_name)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def ingest(self, chat_name: str, messages: list[ChatMessage], persist) -> list[str]:
        state = self._state(chat_name)
        seen = set(state.get("event_ids", []))
        new_messages = [message for message in messages if message.event_id not in seen]
        if not new_messages:
            return []

        saved_ids = list(persist(new_messages))
        state["event_ids"] = list(seen | set(saved_ids))
        state["cursor"] = new_messages[-1].cursor
        self._write_state(chat_name, state)
        return saved_ids


class RoomSelectionStore:
    """Persist short-lived, single-use room choices across Gateway workers."""

    _TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def issue(self, rooms: list[dict[str, object]]) -> list[dict[str, object]]:
        issued = []
        for room in rooms[:10]:
            token = secrets.token_urlsafe(24)
            payload = {
                "chat_id": room["chat_id"],
                "name": room["name"],
                "has_cursor": bool(room["has_cursor"]),
                "expires_at": self._clock() + self._ttl_seconds,
            }
            path = self._root / f"{token}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)
            issued.append({**room, "selection_token": token})
        return issued

    def resolve(self, token: object) -> dict[str, object]:
        if not isinstance(token, str) or not self._TOKEN_PATTERN.fullmatch(token):
            raise ReadOnlyBoundaryError("selection is invalid or expired")
        path = self._root / f"{token}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as error:
            raise ReadOnlyBoundaryError("selection is invalid or expired") from error
        if (
            not isinstance(payload, dict)
            or float(payload.get("expires_at", 0)) < self._clock()
            or not str(payload.get("chat_id", "")).isdecimal()
            or not str(payload.get("name", "")).strip()
        ):
            path.unlink(missing_ok=True)
            raise ReadOnlyBoundaryError("selection is invalid or expired")
        path.unlink()
        return {
            "chat_id": str(payload["chat_id"]),
            "name": str(payload["name"]),
            "has_cursor": bool(payload.get("has_cursor")),
        }


class KakaoCollectionService:
    """Coordinate one explicit, allowlisted, read-only collection request."""

    def __init__(
        self,
        *,
        client: Any,
        raw_store: KakaoObsidianRawStore,
        cursor_store: CursorStore,
    ) -> None:
        self._client = client
        self._raw_store = raw_store
        self._cursor_store = cursor_store

    def recent_rooms(self) -> list[dict[str, object]]:
        candidates = self._client.recent_rooms(limit=10)
        return [
            {
                "chat_id": room.chat_id,
                "name": room.name,
                "has_cursor": self._cursor_store.cursor(room.chat_id) is not None,
            }
            for room in candidates
        ][:10]

    def collect(
        self,
        chat_id: str,
        chat_name: str,
        *,
        initial_baseline: str | None = None,
    ) -> CollectionResult:
        if not chat_id.isdecimal() or not chat_name.strip():
            raise ReadOnlyBoundaryError("selected room is invalid")
        cursor = self._cursor_store.cursor(chat_id)
        if cursor is None:
            if initial_baseline != "current":
                raise ReadOnlyBoundaryError(
                    "first collection requires the current baseline"
                )
            cursor = self._client.latest_cursor(chat_id)
            self._cursor_store.initialize(chat_id, cursor)
            return CollectionResult(chat_id, chat_name, 0, cursor, initialized=True)

        messages = self._client.messages_after(
            chat_id=chat_id,
            chat_name=chat_name,
            cursor=cursor,
        )
        saved = self._cursor_store.ingest(
            chat_id,
            messages,
            lambda batch: self._raw_store.persist(chat_id, batch),
        )
        return CollectionResult(
            chat_id=chat_id,
            chat_name=chat_name,
            collected_count=len(saved),
            cursor=self._cursor_store.cursor(chat_id) or cursor,
        )


def select_recent_allowlisted_rooms(
    rooms: list[ChatRoom], *, allowlist: set[str], limit: int = 10
) -> list[ChatRoom]:
    """Return the most recently active allowed rooms, newest first."""
    if limit <= 0:
        return []
    return sorted(
        (room for room in rooms if room.name in allowlist),
        key=lambda room: (room.last_activity, room.name),
        reverse=True,
    )[:limit]


def validate_collection_request(
    request: CollectionRequest,
    *,
    allowlist: set[str],
    cursors: dict[str, str],
) -> None:
    """Validate an explicit read request before any bridge is contacted."""
    if request.chat_name not in allowlist:
        raise ReadOnlyBoundaryError("chat room is outside the allowlist")
    if request.operation.casefold() != "read":
        raise ReadOnlyBoundaryError("KakaoTalk collection is read-only")
    if not request.request_id.strip() or not request.chat_name.strip():
        raise ReadOnlyBoundaryError("request_id and chat_name are required")
    if request.chat_name not in cursors and not request.initial_baseline:
        raise ReadOnlyBoundaryError(
            "initial baseline is required for the first collection"
        )
