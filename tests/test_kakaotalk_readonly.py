import json
from datetime import UTC, datetime

import pytest

from src.runtime_architecture_v2.kakaotalk_readonly import (
    ChatMessage,
    ChatRoom,
    CollectionRequest,
    CollectionResult,
    CursorStore,
    IrisHttpTransport,
    IrisReadOnlyClient,
    KakaoCollectionService,
    KakaoObsidianRawStore,
    ReadOnlyBoundaryError,
    RoomCandidate,
    RoomSelectionStore,
    select_recent_allowlisted_rooms,
    validate_collection_request,
)


def test_selects_at_most_ten_recent_allowlisted_rooms() -> None:
    rooms = [
        ChatRoom(
            name=f"room-{index}", last_activity=datetime(2026, 7, 1 + index, tzinfo=UTC)
        )
        for index in range(12)
    ]

    selected = select_recent_allowlisted_rooms(
        rooms,
        allowlist={room.name for room in rooms},
    )

    assert [room.name for room in selected] == [
        f"room-{index}" for index in range(11, 1, -1)
    ]


def test_rejects_room_outside_allowlist() -> None:
    request = CollectionRequest(request_id="req-1", chat_name="private")

    with pytest.raises(ReadOnlyBoundaryError, match="allowlist"):
        validate_collection_request(request, allowlist={"approved"}, cursors={})


def test_rejects_outbound_operation() -> None:
    request = CollectionRequest(
        request_id="req-2", chat_name="approved", operation="reply"
    )

    with pytest.raises(ReadOnlyBoundaryError, match="read-only"):
        validate_collection_request(
            request, allowlist={"approved"}, cursors={"approved": "cursor-1"}
        )


def test_ingests_new_messages_and_advances_cursor_after_persistence(tmp_path) -> None:
    store = CursorStore(tmp_path)
    messages = [
        ChatMessage(
            event_id="event-1", chat_name="approved", cursor="cursor-1", message="one"
        ),
        ChatMessage(
            event_id="event-2", chat_name="approved", cursor="cursor-2", message="two"
        ),
    ]

    saved = store.ingest(
        "42", messages, lambda batch: [item.event_id for item in batch]
    )

    assert saved == ["event-1", "event-2"]
    assert store.cursor("42") == "cursor-2"


def test_failed_persistence_does_not_advance_cursor(tmp_path) -> None:
    store = CursorStore(tmp_path)
    messages = [
        ChatMessage(
            event_id="event-1", chat_name="approved", cursor="cursor-1", message="one"
        )
    ]

    with pytest.raises(RuntimeError, match="storage failed"):
        store.ingest(
            "42",
            messages,
            lambda _: (_ for _ in ()).throw(RuntimeError("storage failed")),
        )

    assert store.cursor("42") is None


def test_reingesting_same_event_is_idempotent(tmp_path) -> None:
    store = CursorStore(tmp_path)
    message = ChatMessage(
        event_id="event-1", chat_name="approved", cursor="cursor-1", message="one"
    )
    calls = []

    def persist(batch):
        ids = [item.event_id for item in batch]
        calls.extend(ids)
        return ids

    store.ingest("42", [message], persist)
    store.ingest("42", [message], persist)

    assert calls == ["event-1"]


def test_iris_client_returns_ten_recent_named_rooms() -> None:
    captured = []

    def transport(path, payload):
        captured.append((path, payload))
        return {
            "data": [
                {
                    "id": str(index),
                    "type": "MultiChat",
                    "last_updated_at": str(100 - index),
                    "room_name": f"room-{index}",
                }
                for index in range(12)
            ]
        }

    rooms = IrisReadOnlyClient(transport).recent_rooms(limit=10)

    assert len(rooms) == 10
    assert rooms[0].name == "room-0"
    assert captured[0][0] == "/query"
    assert "last_message" not in captured[0][1]["query"]


def test_iris_client_uses_event_name_cache_for_direct_room() -> None:
    def transport(path, payload):
        return {
            "data": [
                {
                    "id": "42",
                    "type": "DirectChat",
                    "last_updated_at": "100",
                    "room_name": None,
                }
            ]
        }

    client = IrisReadOnlyClient(transport, room_name_cache={"42": "cached direct"})

    assert client.recent_rooms()[0].name == "cached direct"


def test_iris_client_drops_unnamed_room_instead_of_exposing_id() -> None:
    def transport(path, payload):
        return {
            "data": [
                {
                    "id": "42",
                    "type": "DirectChat",
                    "last_updated_at": "100",
                    "room_name": None,
                }
            ]
        }

    assert IrisReadOnlyClient(transport).recent_rooms() == []


def test_iris_client_reads_only_messages_after_room_cursor() -> None:
    captured = []

    def transport(path, payload):
        captured.append((path, payload))
        return {
            "data": [
                {
                    "_id": "101",
                    "chat_id": "42",
                    "user_id": "7",
                    "message": "hello",
                    "created_at": "1700000000",
                    "attachment": None,
                }
            ]
        }

    messages = IrisReadOnlyClient(transport).messages_after(
        chat_id="42",
        chat_name="allowed",
        cursor="100",
    )

    assert [message.event_id for message in messages] == ["101"]
    assert messages[0].sender_id == "7"
    assert captured[0][0] == "/query"
    assert captured[0][1]["bind"] == ["42", "100", "500"]
    assert "_id > ?" in captured[0][1]["query"]


def test_iris_client_rejects_non_numeric_cursor() -> None:
    client = IrisReadOnlyClient(lambda path, payload: {"data": []})

    with pytest.raises(ReadOnlyBoundaryError, match="numeric"):
        client.messages_after(chat_id="42", chat_name="allowed", cursor="bad")


def test_http_transport_rejects_non_query_path() -> None:
    transport = IrisHttpTransport("http://127.0.0.1:3000")

    with pytest.raises(ReadOnlyBoundaryError, match="/query"):
        transport("/reply", {"room": "42", "data": "no"})


def test_http_transport_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(ReadOnlyBoundaryError, match="loopback"):
        IrisHttpTransport("http://168.107.17.27:3000")


def test_raw_store_persists_message_under_existing_chat_logs_layout(tmp_path) -> None:
    store = KakaoObsidianRawStore(tmp_path)
    message = ChatMessage(
        event_id="101",
        chat_name="allowed",
        cursor="101",
        message="hello",
        sender_id="7",
        sent_at="1700000000",
    )

    saved = store.persist("42", [message])

    assert saved == ["101"]
    payload = json.loads(
        (tmp_path / "raw/chat-logs/kakaotalk/42/101.json").read_text(encoding="utf-8")
    )
    assert payload["source"] == "kakaotalk"
    assert payload["message"] == "hello"


def test_raw_store_rejects_non_numeric_identifiers(tmp_path) -> None:
    store = KakaoObsidianRawStore(tmp_path)
    message = ChatMessage(
        event_id="../escape",
        chat_name="allowed",
        cursor="../escape",
        message="hello",
    )

    with pytest.raises(ReadOnlyBoundaryError, match="numeric"):
        store.persist("42", [message])


def test_collection_service_lists_current_recent_named_rooms(tmp_path) -> None:
    client = type(
        "Client",
        (),
        {
            "recent_rooms": lambda self, limit=10: [
                RoomCandidate("42", "approved", "MultiChat", 200),
                RoomCandidate("99", "private", "MultiChat", 100),
            ]
        },
    )()
    service = KakaoCollectionService(
        client=client,
        raw_store=KakaoObsidianRawStore(tmp_path / "vault"),
        cursor_store=CursorStore(tmp_path / "cursors"),
    )

    assert service.recent_rooms() == [
        {
            "chat_id": "42",
            "name": "approved",
            "has_cursor": False,
        },
        {
            "chat_id": "99",
            "name": "private",
            "has_cursor": False,
        },
    ]


def test_first_collection_can_initialize_at_current_cursor_without_saving(
    tmp_path,
) -> None:
    class Client:
        def latest_cursor(self, chat_id):
            assert chat_id == "42"
            return "120"

        def messages_after(self, **kwargs):
            raise AssertionError("baseline initialization must not read messages")

    cursors = CursorStore(tmp_path / "cursors")
    service = KakaoCollectionService(
        client=Client(),
        raw_store=KakaoObsidianRawStore(tmp_path / "vault"),
        cursor_store=cursors,
    )

    result = service.collect("42", "approved", initial_baseline="current")

    assert result == CollectionResult(
        chat_id="42",
        chat_name="approved",
        collected_count=0,
        cursor="120",
        initialized=True,
    )
    assert cursors.cursor("42") == "120"


def test_collection_resumes_after_durable_cursor_and_persists_raw(tmp_path) -> None:
    class Client:
        def messages_after(self, **kwargs):
            assert kwargs["cursor"] == "100"
            return [
                ChatMessage(
                    event_id="101",
                    chat_name="approved",
                    cursor="101",
                    message="new",
                )
            ]

    cursors = CursorStore(tmp_path / "cursors")
    cursors.initialize("42", "100")
    service = KakaoCollectionService(
        client=Client(),
        raw_store=KakaoObsidianRawStore(tmp_path / "vault"),
        cursor_store=cursors,
    )

    result = service.collect("42", "approved")

    assert result.collected_count == 1
    assert result.cursor == "101"
    assert (tmp_path / "vault/raw/chat-logs/kakaotalk/42/101.json").is_file()


def test_room_selection_token_expires_and_is_single_use(tmp_path) -> None:
    now = [1000.0]
    selections = RoomSelectionStore(
        tmp_path / "selections",
        ttl_seconds=60,
        clock=lambda: now[0],
    )
    issued = selections.issue(
        [{"chat_id": "42", "name": "approved", "has_cursor": True}]
    )
    token = issued[0]["selection_token"]

    assert selections.resolve(token) == {
        "chat_id": "42",
        "name": "approved",
        "has_cursor": True,
    }
    with pytest.raises(ReadOnlyBoundaryError, match="invalid or expired"):
        selections.resolve(token)

    expired = selections.issue(
        [{"chat_id": "43", "name": "later", "has_cursor": False}]
    )[0]["selection_token"]
    now[0] = 1061.0
    with pytest.raises(ReadOnlyBoundaryError, match="invalid or expired"):
        selections.resolve(expired)


def test_room_selection_rejects_malformed_token_without_path_escape(tmp_path) -> None:
    selections = RoomSelectionStore(tmp_path / "selections")

    with pytest.raises(ReadOnlyBoundaryError, match="invalid or expired"):
        selections.resolve("../42")
