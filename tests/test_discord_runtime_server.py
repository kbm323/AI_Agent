from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any

from nacl.signing import SigningKey

from src.discord_runtime_server import make_runtime_request_handler


def _signed_request(payload: dict[str, Any], signing_key: SigningKey):
    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = "1234567890"
    signature = signing_key.sign(timestamp.encode("utf-8") + raw_body).signature.hex()
    return raw_body, signature, timestamp


def test_runtime_request_handler_accepts_signed_meeting_post(tmp_path):
    signing_key = SigningKey.generate()
    started: list[dict[str, Any]] = []
    handler_cls = make_runtime_request_handler(
        public_key=signing_key.verify_key.encode().hex(),
        start_smoke=started.append,
    )

    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = {
            "id": "1",
            "token": "token-1",
            "type": 2,
            "channel_id": "channel-1",
            "data": {
                "name": "meeting",
                "options": [{"name": "topic", "type": 3, "value": "회의해줘"}],
            },
        }
        raw_body, signature, timestamp = _signed_request(payload, signing_key)
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/discord/interactions",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": signature,
                "X-Signature-Timestamp": timestamp,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            status = response.status
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert body == {"type": 5}
    assert len(started) == 1
    assert started[0]["thread_id"] == "channel-1"
