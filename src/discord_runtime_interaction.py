"""Discord interaction bridge for runtime smoke execution.

This module connects Discord's signed interaction webhook boundary to the
existing runtime smoke live pipeline.  It intentionally keeps the HTTP server
and the smoke runner injectable so tests can exercise the Discord contract
without network calls or real subprocesses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.discord_webhook_handler import (
    WebhookRequest,
    build_deferred_response,
    build_error_response,
    handle_webhook,
)

SmokeStarter = Callable[[dict[str, Any]], Any]


def handle_runtime_interaction_request(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    public_key: str,
    start_smoke: SmokeStarter,
) -> tuple[int, dict[str, Any]]:
    """Handle one signed Discord interaction request.

    Returns an HTTP status code plus JSON-serializable body.  PING requests are
    answered with PONG.  ``/meeting`` application commands are acknowledged with
    a deferred response and handed to ``start_smoke`` for background execution.
    """

    result = handle_webhook(
        WebhookRequest(
            raw_body=raw_body,
            signature=_header(headers, "X-Signature-Ed25519"),
            timestamp=_header(headers, "X-Signature-Timestamp"),
        ),
        public_key,
    )
    if not result.success:
        return 401, {"ok": False, "error": result.error or "webhook rejected"}

    if result.response is not None:
        return 200, result.response.to_dict()

    parsed = result.parsed_interaction
    if parsed is None:
        return 400, {"ok": False, "error": "missing parsed interaction"}

    if parsed.command_name != "meeting":
        return 200, build_error_response(
            f"Unknown command: /{parsed.command_name}",
            ephemeral=True,
        ).to_dict()

    payload = dict(parsed.raw_payload)
    payload.setdefault("thread_id", parsed.channel_id)
    start_smoke(payload)
    return 200, build_deferred_response().to_dict()


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


__all__ = ["SmokeStarter", "handle_runtime_interaction_request"]
