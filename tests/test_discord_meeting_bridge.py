from __future__ import annotations

from src.discord_meeting_bridge import build_meeting_handler, build_meeting_registry
from src.priority_queue import PriorityMeetingQueue


def _meeting_payload(topic: str = "기술 회의 열어줘. API 장애 대응") -> dict[str, object]:
    return {
        "type": 2,
        "id": "interaction-1",
        "token": "token-1",
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "member": {"user": {"id": "user-1"}},
        "data": {
            "name": "meeting",
            "options": [
                {"name": "topic", "value": topic},
                {"name": "result_channel_id", "value": "results-1"},
            ],
        },
    }


def test_meeting_handler_bridges_discord_payload_to_pipeline(tmp_path):
    queue = PriorityMeetingQueue(max_concurrent=2)
    handler = build_meeting_handler(queue=queue, meetings_root=str(tmp_path))

    response = handler(_meeting_payload())

    assert response["type"] == 4
    assert "회의가 접수되었습니다" in response["data"]["content"]
    assert len(queue.running_ids) == 1


def test_meeting_handler_returns_user_error_when_topic_missing(tmp_path):
    queue = PriorityMeetingQueue(max_concurrent=2)
    handler = build_meeting_handler(queue=queue, meetings_root=str(tmp_path))
    payload = _meeting_payload(topic="")

    response = handler(payload)

    assert response["type"] == 4
    assert "회의 주제가 비어 있습니다" in response["data"]["content"]
    assert response["data"]["flags"] == 64
    assert queue.running_ids == ()


def test_build_meeting_registry_registers_meeting_handler(tmp_path):
    queue = PriorityMeetingQueue(max_concurrent=2)

    registry = build_meeting_registry(queue=queue, meetings_root=str(tmp_path))

    assert "meeting" in registry
    route = registry.get("meeting")
    assert route is not None
    response = route(_meeting_payload("회의 열어줘. 신규 앨범 전략"))
    assert response["type"] == 4
    assert len(queue.running_ids) == 1
