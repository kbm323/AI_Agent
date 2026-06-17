from __future__ import annotations

import json
from typing import Any

from nacl.signing import SigningKey

from src.discord_runtime_interaction import handle_runtime_interaction_request


def _signed_request(payload: dict[str, Any], signing_key: SigningKey):
    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = "1234567890"
    signature = signing_key.sign(timestamp.encode("utf-8") + raw_body).signature.hex()
    return raw_body, {
        "X-Signature-Ed25519": signature,
        "X-Signature-Timestamp": timestamp,
    }


def test_ping_interaction_returns_pong_without_starting_smoke():
    signing_key = SigningKey.generate()
    raw_body, headers = _signed_request(
        {"id": "1", "token": "token-1", "type": 1},
        signing_key,
    )
    started: list[dict[str, Any]] = []

    status, body = handle_runtime_interaction_request(
        raw_body=raw_body,
        headers=headers,
        public_key=signing_key.verify_key.encode().hex(),
        start_smoke=started.append,
    )

    assert status == 200
    assert body == {"type": 1}
    assert started == []


def test_meeting_command_returns_deferred_response_and_starts_smoke():
    signing_key = SigningKey.generate()
    payload = {
        "id": "2",
        "token": "token-2",
        "type": 2,
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "member": {"user": {"id": "user-1"}},
        "data": {
            "name": "meeting",
            "options": [{"name": "topic", "type": 3, "value": "라이브 회의해줘"}],
        },
    }
    raw_body, headers = _signed_request(payload, signing_key)
    started: list[dict[str, Any]] = []

    status, body = handle_runtime_interaction_request(
        raw_body=raw_body,
        headers=headers,
        public_key=signing_key.verify_key.encode().hex(),
        start_smoke=started.append,
    )

    assert status == 200
    assert body == {"type": 5}
    assert len(started) == 1
    assert started[0]["thread_id"] == "channel-1"
    assert started[0]["data"]["options"][0]["value"] == "라이브 회의해줘"


def test_bad_signature_returns_unauthorized_without_starting_smoke():
    good_key = SigningKey.generate()
    bad_key = SigningKey.generate()
    raw_body, headers = _signed_request(
        {"id": "3", "token": "token-3", "type": 1},
        bad_key,
    )
    started: list[dict[str, Any]] = []

    status, body = handle_runtime_interaction_request(
        raw_body=raw_body,
        headers=headers,
        public_key=good_key.verify_key.encode().hex(),
        start_smoke=started.append,
    )

    assert status == 401
    assert body["ok"] is False
    assert "signature" in body["error"].lower()
    assert started == []
