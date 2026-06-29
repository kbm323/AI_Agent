from __future__ import annotations

from src.discord_meeting_bridge import handle_discord_meeting_request


def _meeting_payload(topic: str = "기술 회의. API 장애 대응") -> dict[str, object]:
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


def test_meeting_handler_bridges_discord_payload(tmp_path):
    response = handle_discord_meeting_request(_meeting_payload())

    assert response["type"] == 4
    assert "회의" in response["data"]["content"]


def test_meeting_handler_returns_user_error_when_topic_missing():
    payload = _meeting_payload(topic="")

    response = handle_discord_meeting_request(payload)

    assert response["type"] == 4
    assert "회의 주제가 비어 있습니다" in response["data"]["content"]
    assert response["data"]["flags"] == 64
