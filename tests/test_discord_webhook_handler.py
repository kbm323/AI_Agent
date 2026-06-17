"""Tests for the Discord interaction webhook handler (Sub-AC 1.1b).

Coverage categories
-------------------
1. Ed25519 signature verification — valid, invalid, edge cases
2. Interaction payload parsing — valid, missing fields, json errors
3. PING handling — returns PONG response with correct metadata
4. APPLICATION_COMMAND handling — command extraction, option flattening
5. Command routing — register / unregister / clear / route / handler errors
6. Response builders — deferred, error, success, ack, ephemeral flags
7. End-to-end handle_webhook — full pipeline with real crypto
8. Error types — structured errors with machine-readable codes
"""

from __future__ import annotations

import json

import nacl.signing
import pytest

from src.discord_webhook_handler import (
    DiscordInteractionType,
    DiscordMessageFlags,
    DiscordResponseType,
    InteractionResponse,
    InvalidPayloadError,
    ParsedInteraction,
    SignatureVerificationError,
    WebhookHandlerError,
    WebhookRequest,
    WebhookResult,
    build_ack_response,
    build_deferred_response,
    build_error_response,
    build_success_response,
    clear_command_handlers,
    handle_webhook,
    parse_interaction_payload,
    register_command_handler,
    route_command,
    unregister_command_handler,
    verify_discord_signature,
)

# ═══════════════════════════════════════════════════════════════════════════
# Ed25519 key pair for testing (generated fresh per test run)
# ═══════════════════════════════════════════════════════════════════════════


def _generate_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 key pair for testing.

    Returns (private_key_hex, public_key_hex) each as hex strings.
    """
    sk = nacl.signing.SigningKey.generate()
    vk = sk.verify_key
    return sk.encode(nacl.encoding.HexEncoder).decode(), vk.encode(nacl.encoding.HexEncoder).decode()


def _sign_body(
    raw_body: bytes,
    timestamp: str,
    private_key_hex: str,
) -> str:
    """Sign a body+timestamp pair with the given private key.

    Returns the hex-encoded signature.
    """
    sk = nacl.signing.SigningKey(private_key_hex, encoder=nacl.encoding.HexEncoder)
    message = timestamp.encode("utf-8") + raw_body
    return sk.sign(message).signature.hex()


# ═══════════════════════════════════════════════════════════════════════════
# Test fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def keypair() -> tuple[str, str]:
    """A fresh Ed25519 key pair (private, public)."""
    return _generate_keypair()


@pytest.fixture
def public_key(keypair: tuple[str, str]) -> str:
    """A valid 64-char hex public key."""
    return keypair[1]


@pytest.fixture
def private_key(keypair: tuple[str, str]) -> str:
    """A valid 64-char hex private key for signing."""
    return keypair[0]


def _make_ping_payload() -> dict:
    """Return a minimal, valid Discord PING interaction payload."""
    return {"id": "interaction_ping_1", "token": "tok_ping", "type": 1, "version": 1}


def _make_slash_payload(
    command_name: str = "meeting",
    options: list[dict] | None = None,
    *,
    interaction_id: str = "interaction_cmd_1",
    token: str = "tok_cmd",
    user_id: str = "user_123",
    channel_id: str = "channel_456",
    guild_id: str = "guild_789",
) -> dict:
    """Return a valid Discord APPLICATION_COMMAND interaction payload."""
    payload: dict = {
        "id": interaction_id,
        "token": token,
        "type": 2,
        "version": 1,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "member": {"user": {"id": user_id, "username": "test_user"}},
        "data": {
            "id": "data_id",
            "name": command_name,
            "type": 1,
        },
    }
    if options:
        payload["data"]["options"] = options  # type: ignore[index]
    return payload


def _make_webhook_request(
    payload: dict,
    private_key_hex: str,
    *,
    timestamp: str = "1234567890",
) -> WebhookRequest:
    """Create a signed WebhookRequest from a payload dict."""
    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signature = _sign_body(raw_body, timestamp, private_key_hex)
    return WebhookRequest(raw_body=raw_body, signature=signature, timestamp=timestamp)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Ed25519 signature verification
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifyDiscordSignature:
    """Tests for verify_discord_signature."""

    def test_valid_signature_passes(self, keypair: tuple[str, str]) -> None:
        private, public = keypair
        body = b'{"type":1}'
        timestamp = "1234567890"
        signature = _sign_body(body, timestamp, private)

        result = verify_discord_signature(body, signature, timestamp, public)
        assert result is True

    def test_wrong_body_fails(self, keypair: tuple[str, str]) -> None:
        private, public = keypair
        body = b'{"type":1}'
        timestamp = "1234567890"
        signature = _sign_body(body, timestamp, private)

        with pytest.raises(SignatureVerificationError, match="verification failed"):
            verify_discord_signature(b'{"type":2}', signature, timestamp, public)

    def test_wrong_timestamp_fails(self, keypair: tuple[str, str]) -> None:
        private, public = keypair
        body = b'{"type":1}'
        timestamp = "1234567890"
        signature = _sign_body(body, timestamp, private)

        with pytest.raises(SignatureVerificationError, match="verification failed"):
            verify_discord_signature(body, signature, "9999999999", public)

    def test_tampered_signature_fails(self, keypair: tuple[str, str]) -> None:
        private, public = keypair
        body = b'{"type":1}'
        timestamp = "1234567890"
        signature = _sign_body(body, timestamp, private)

        # Flip the first hex character
        tampered = "f" + signature[1:] if signature[0] != "f" else "0" + signature[1:]

        with pytest.raises(SignatureVerificationError, match="verification failed"):
            verify_discord_signature(body, tampered, timestamp, public)

    def test_wrong_public_key_fails(self, keypair: tuple[str, str]) -> None:
        private, public = keypair
        # Generate a *different* key pair for verification
        other_private, other_public = _generate_keypair()
        body = b'{"type":1}'
        timestamp = "1234567890"
        signature = _sign_body(body, timestamp, private)

        with pytest.raises(SignatureVerificationError, match="verification failed"):
            verify_discord_signature(body, signature, timestamp, other_public)

    def test_empty_signature_raises(self, public_key: str) -> None:
        with pytest.raises(SignatureVerificationError, match="Missing"):
            verify_discord_signature(b"{}", "", "123", public_key)

    def test_empty_timestamp_raises(self, public_key: str) -> None:
        with pytest.raises(SignatureVerificationError, match="Missing"):
            verify_discord_signature(b"{}", "aa" * 64, "", public_key)

    def test_empty_public_key_raises(self) -> None:
        with pytest.raises(SignatureVerificationError, match="Missing"):
            verify_discord_signature(b"{}", "aa" * 64, "123", "")

    def test_invalid_hex_public_key_raises(self) -> None:
        with pytest.raises(SignatureVerificationError, match="not valid hex"):
            verify_discord_signature(b"{}", "aa" * 64, "123", "zz" * 32)

    def test_wrong_length_public_key_raises(self) -> None:
        with pytest.raises(SignatureVerificationError, match="must be 32 bytes"):
            verify_discord_signature(b"{}", "aa" * 64, "123", "aa" * 10)

    def test_invalid_hex_signature_raises(self, public_key: str) -> None:
        with pytest.raises(SignatureVerificationError, match="not valid hex"):
            verify_discord_signature(b"{}", "zz" * 64, "123", public_key)

    def test_wrong_length_signature_raises(self, public_key: str) -> None:
        with pytest.raises(SignatureVerificationError, match="expected 64"):
            verify_discord_signature(b"{}", "aa" * 10, "123", public_key)

    def test_code_property_on_error(self, public_key: str) -> None:
        try:
            verify_discord_signature(b"{}", "aa" * 64, "123", public_key)
        except SignatureVerificationError as exc:
            assert exc.code == "SIGNATURE_VERIFICATION_FAILED"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Interaction payload parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestParseInteractionPayload:
    """Tests for parse_interaction_payload."""

    def test_valid_ping_payload(self) -> None:
        payload = _make_ping_payload()
        raw = json.dumps(payload).encode("utf-8")
        result = parse_interaction_payload(raw)
        assert result["type"] == 1
        assert result["id"] == "interaction_ping_1"

    def test_valid_slash_payload(self) -> None:
        payload = _make_slash_payload()
        raw = json.dumps(payload).encode("utf-8")
        result = parse_interaction_payload(raw)
        assert result["type"] == 2
        assert result["data"]["name"] == "meeting"

    def test_missing_id_raises(self) -> None:
        payload = {"token": "tok", "type": 2}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="missing valid 'id'"):
            parse_interaction_payload(raw)

    def test_empty_id_raises(self) -> None:
        payload = {"id": "", "token": "tok", "type": 2}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="missing valid 'id'"):
            parse_interaction_payload(raw)

    def test_non_string_id_raises(self) -> None:
        payload = {"id": 12345, "token": "tok", "type": 2}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="missing valid 'id'"):
            parse_interaction_payload(raw)

    def test_missing_token_raises(self) -> None:
        payload = {"id": "id1", "type": 2}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="missing valid 'token'"):
            parse_interaction_payload(raw)

    def test_empty_token_raises(self) -> None:
        payload = {"id": "id1", "token": "", "type": 2}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="missing valid 'token'"):
            parse_interaction_payload(raw)

    def test_missing_type_raises(self) -> None:
        payload = {"id": "id1", "token": "tok"}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="missing or invalid 'type'"):
            parse_interaction_payload(raw)

    def test_unknown_type_raises(self) -> None:
        payload = {"id": "id1", "token": "tok", "type": 99}
        raw = json.dumps(payload).encode("utf-8")
        with pytest.raises(InvalidPayloadError, match="Unknown interaction type: 99"):
            parse_interaction_payload(raw)

    def test_invalid_json_raises(self) -> None:
        raw = b"not valid json {{{"
        with pytest.raises(InvalidPayloadError, match="Failed to parse"):
            parse_interaction_payload(raw)

    def test_non_dict_payload_raises(self) -> None:
        raw = b"[1, 2, 3]"
        with pytest.raises(InvalidPayloadError, match="not a JSON object"):
            parse_interaction_payload(raw)

    def test_invalid_utf8_raises(self) -> None:
        raw = b"\x80\x81\x82"  # invalid UTF-8
        with pytest.raises(InvalidPayloadError, match="not valid UTF-8"):
            parse_interaction_payload(raw)


# ═══════════════════════════════════════════════════════════════════════════
# 3. PING handling
# ═══════════════════════════════════════════════════════════════════════════


class TestHandlePing:
    """Tests for PING (type 1) webhook handling."""

    def test_ping_returns_pong(self, private_key: str, public_key: str) -> None:
        payload = _make_ping_payload()
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.success is True
        assert result.response is not None
        assert result.response.type == DiscordResponseType.PONG
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.type == DiscordInteractionType.PING

    def test_ping_parsed_interaction_has_correct_metadata(
        self, private_key: str, public_key: str
    ) -> None:
        payload = {
            "id": "ping_1",
            "token": "tok_ping_1",
            "type": 1,
            "channel_id": "ch_123",
            "guild_id": "guild_456",
            "user": {"id": "user_789"},
        }
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        parsed = result.parsed_interaction
        assert parsed is not None
        assert parsed.interaction_id == "ping_1"
        assert parsed.interaction_token == "tok_ping_1"
        assert parsed.type == DiscordInteractionType.PING
        assert parsed.user_id == "user_789"
        assert parsed.channel_id == "ch_123"
        assert parsed.guild_id == "guild_456"


# ═══════════════════════════════════════════════════════════════════════════
# 4. APPLICATION_COMMAND handling
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleApplicationCommand:
    """Tests for APPLICATION_COMMAND (type 2) webhook handling."""

    def test_extracts_command_name(self, private_key: str, public_key: str) -> None:
        payload = _make_slash_payload(command_name="meeting")
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.success is True
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.command_name == "meeting"
        # APPLICATION_COMMAND returns no immediate response — routing is
        # done by the caller later.
        assert result.response is None

    def test_extracts_command_options(self, private_key: str, public_key: str) -> None:
        payload = _make_slash_payload(
            command_name="meeting",
            options=[
                {"name": "agenda", "type": 3, "value": "Hello World"},
                {"name": "priority", "type": 3, "value": "P1"},
            ],
        )
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        parsed = result.parsed_interaction
        assert parsed is not None
        assert parsed.command_options == {"agenda": "Hello World", "priority": "P1"}

    def test_extracts_user_from_member(self, private_key: str, public_key: str) -> None:
        payload = _make_slash_payload(user_id="user_999")
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.user_id == "user_999"

    def test_extracts_user_from_top_level(self, private_key: str, public_key: str) -> None:
        """DM interactions have user at top level, not nested under member."""
        payload = {
            "id": "dm_cmd",
            "token": "tok_dm",
            "type": 2,
            "user": {"id": "dm_user_1", "username": "dm_user"},
            "data": {"id": "d1", "name": "meeting", "type": 1},
        }
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.user_id == "dm_user_1"

    def test_missing_data_returns_failure(self, private_key: str, public_key: str) -> None:
        payload = {
            "id": "no_data",
            "token": "tok",
            "type": 2,
            "user": {"id": "u1"},
        }
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.success is False
        assert "missing valid 'data'" in (result.error or "")

    def test_extracts_channel_id(self, private_key: str, public_key: str) -> None:
        payload = _make_slash_payload(channel_id="channel_xyz")
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.channel_id == "channel_xyz"

    def test_nested_channel_object(self, private_key: str, public_key: str) -> None:
        """Discord sometimes sends channel as nested object with just an id."""
        payload = {
            "id": "cmd_nested_ch",
            "token": "tok",
            "type": 2,
            "channel": {"id": "nested_chan"},
            "user": {"id": "u1"},
            "data": {"id": "d1", "name": "meeting", "type": 1},
        }
        request = _make_webhook_request(payload, private_key)
        result = handle_webhook(request, public_key)
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.channel_id == "nested_chan"

    def test_flattens_subcommand_options(self, private_key: str, public_key: str) -> None:
        """Subcommands (type 1) nest their options — ensure flattening works."""
        payload = _make_slash_payload(
            command_name="meeting",
            options=[
                {
                    "name": "create",
                    "type": 1,  # SUB_COMMAND
                    "options": [
                        {"name": "topic", "type": 3, "value": "Design Review"},
                        {"name": "team", "type": 3, "value": "Art"},
                    ],
                }
            ],
        )
        request = _make_webhook_request(payload, private_key)
        result = handle_webhook(request, public_key)
        parsed = result.parsed_interaction
        assert parsed is not None
        assert parsed.command_options == {"topic": "Design Review", "team": "Art"}

    def test_flattens_subcommand_group_options(self, private_key: str, public_key: str) -> None:
        """Subcommand groups (type 2) nest further — ensure double flattening."""
        payload = _make_slash_payload(
            command_name="meeting",
            options=[
                {
                    "name": "admin",
                    "type": 2,  # SUB_COMMAND_GROUP
                    "options": [
                        {
                            "name": "config",
                            "type": 1,  # SUB_COMMAND
                            "options": [
                                {"name": "key", "type": 3, "value": "max_rounds"},
                                {"name": "value", "type": 3, "value": "5"},
                            ],
                        }
                    ],
                }
            ],
        )
        request = _make_webhook_request(payload, private_key)
        result = handle_webhook(request, public_key)
        parsed = result.parsed_interaction
        assert parsed is not None
        assert parsed.command_options == {"key": "max_rounds", "value": "5"}

    def test_user_unknown_when_missing(self, private_key: str, public_key: str) -> None:
        """If no user info in payload, returns 'unknown'."""
        payload = {
            "id": "no_user",
            "token": "tok",
            "type": 2,
            "data": {"id": "d1", "name": "meeting", "type": 1},
        }
        request = _make_webhook_request(payload, private_key)
        result = handle_webhook(request, public_key)
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.user_id == "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Command routing
# ═══════════════════════════════════════════════════════════════════════════


class TestCommandRouting:
    """Tests for register/unregister/clear/route_command."""

    def setup_method(self) -> None:
        """Clear handlers before each test."""
        clear_command_handlers()

    def teardown_method(self) -> None:
        """Clear handlers after each test."""
        clear_command_handlers()

    def test_register_and_route(self) -> None:
        def handler(p: ParsedInteraction) -> InteractionResponse:
            return build_success_response(f"Got: {p.command_name}")

        register_command_handler("meeting", handler)

        parsed = ParsedInteraction(
            interaction_id="i1",
            interaction_token="t1",
            type=DiscordInteractionType.APPLICATION_COMMAND,
            user_id="u1",
            channel_id="c1",
            command_name="meeting",
        )

        response = route_command(parsed)
        assert response.type == DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE
        assert response.data is not None
        assert response.data["content"] == "Got: meeting"

    def test_unregister_command(self) -> None:
        def handler(p: ParsedInteraction) -> InteractionResponse:
            return build_success_response("ok")

        register_command_handler("test", handler)
        assert unregister_command_handler("test") is True
        assert unregister_command_handler("test") is False

    def test_clear_all_handlers(self) -> None:
        def handler(p: ParsedInteraction) -> InteractionResponse:
            return build_success_response("ok")

        register_command_handler("a", handler)
        register_command_handler("b", handler)
        clear_command_handlers()

        parsed = ParsedInteraction(
            interaction_id="i1",
            interaction_token="t1",
            type=DiscordInteractionType.APPLICATION_COMMAND,
            user_id="u1",
            channel_id="c1",
            command_name="a",
        )
        response = route_command(parsed)
        assert "Unknown command" in (response.data or {}).get("content", "")

    def test_unknown_command_returns_error(self) -> None:
        parsed = ParsedInteraction(
            interaction_id="i1",
            interaction_token="t1",
            type=DiscordInteractionType.APPLICATION_COMMAND,
            user_id="u1",
            channel_id="c1",
            command_name="nonexistent",
        )
        response = route_command(parsed)
        assert "Unknown command" in (response.data or {}).get("content", "")
        assert response.data is not None
        assert response.data.get("flags") == DiscordMessageFlags.EPHEMERAL

    def test_handler_exception_returns_error(self) -> None:
        def failing_handler(p: ParsedInteraction) -> InteractionResponse:
            raise RuntimeError("handler exploded")

        register_command_handler("explode", failing_handler)

        parsed = ParsedInteraction(
            interaction_id="i1",
            interaction_token="t1",
            type=DiscordInteractionType.APPLICATION_COMMAND,
            user_id="u1",
            channel_id="c1",
            command_name="explode",
        )
        response = route_command(parsed)
        assert response.data is not None
        assert "handler exploded" in response.data["content"]
        assert response.data.get("flags") == DiscordMessageFlags.EPHEMERAL

    def test_register_empty_name_raises(self) -> None:
        def handler(p: ParsedInteraction) -> InteractionResponse:
            return build_success_response("ok")

        with pytest.raises(ValueError, match="command_name must not be empty"):
            register_command_handler("", handler)

    def test_register_whitespace_name_raises(self) -> None:
        def handler(p: ParsedInteraction) -> InteractionResponse:
            return build_success_response("ok")

        with pytest.raises(ValueError, match="command_name must not be empty"):
            register_command_handler("   ", handler)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Response builders
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseBuilders:
    """Tests for build_deferred_response, build_error_response, etc."""

    def test_deferred_response(self) -> None:
        r = build_deferred_response()
        assert r.type == DiscordResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        assert r.data is None
        d = r.to_dict()
        assert d == {"type": 5}

    def test_error_response_ephemeral(self) -> None:
        r = build_error_response("Something went wrong", ephemeral=True)
        assert r.type == DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE
        assert r.data is not None
        assert r.data["content"] == "Something went wrong"
        assert r.data["flags"] == DiscordMessageFlags.EPHEMERAL

    def test_error_response_public(self) -> None:
        r = build_error_response("Public error", ephemeral=False)
        assert r.data is not None
        assert "flags" not in r.data

    def test_success_response_public(self) -> None:
        r = build_success_response("All good!")
        assert r.type == DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE
        assert r.data is not None
        assert r.data["content"] == "All good!"
        assert "flags" not in r.data

    def test_success_response_ephemeral(self) -> None:
        r = build_success_response("Secret success", ephemeral=True)
        assert r.data is not None
        assert r.data["flags"] == DiscordMessageFlags.EPHEMERAL

    def test_ack_response(self) -> None:
        r = build_ack_response()
        assert r.type == DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE
        assert r.data == {"content": ""}

    def test_to_json_produces_valid_json(self) -> None:
        r = build_error_response("test")
        raw = r.to_json()
        parsed = json.loads(raw)
        assert parsed["type"] == 4
        assert parsed["data"]["content"] == "test"

    def test_to_dict_returns_dict(self) -> None:
        r = build_success_response("hello")
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["type"] == 4
        assert d["data"]["content"] == "hello"


# ═══════════════════════════════════════════════════════════════════════════
# 7. End-to-end handle_webhook
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleWebhookEndToEnd:
    """Full pipeline tests for handle_webhook."""

    def test_valid_ping_e2e(self, private_key: str, public_key: str) -> None:
        payload = _make_ping_payload()
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.success is True
        assert result.response is not None
        assert result.response.to_dict() == {"type": 1}
        assert result.error is None

    def test_valid_slash_command_e2e(self, private_key: str, public_key: str) -> None:
        payload = _make_slash_payload(
            command_name="meeting",
            options=[{"name": "agenda", "type": 3, "value": "Plan sprint"}],
        )
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.success is True
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.command_name == "meeting"
        assert result.parsed_interaction.command_options == {"agenda": "Plan sprint"}

    def test_invalid_signature_fails(self, public_key: str) -> None:
        payload = _make_ping_payload()
        raw_body = json.dumps(payload).encode("utf-8")
        request = WebhookRequest(
            raw_body=raw_body,
            signature="aa" * 64,  # intentionally wrong signature
            timestamp="1234567890",
        )

        result = handle_webhook(request, public_key)
        assert result.success is False
        assert result.error is not None
        assert "verification failed" in result.error

    def test_invalid_payload_fails(self, private_key: str, public_key: str) -> None:
        """Payload missing required fields should fail after signature check."""
        raw_body = b'{"type": 99}'  # invalid payload
        timestamp = "1234567890"
        signature = _sign_body(raw_body, timestamp, private_key)

        request = WebhookRequest(
            raw_body=raw_body, signature=signature, timestamp=timestamp
        )
        result = handle_webhook(request, public_key)
        assert result.success is False
        assert "missing valid 'id'" in (result.error or "")

    def test_result_is_always_webhookresult(self, private_key: str, public_key: str) -> None:
        """handle_webhook never returns None — both success and failure paths."""
        # Success
        payload = _make_ping_payload()
        request = _make_webhook_request(payload, private_key)
        result = handle_webhook(request, public_key)
        assert isinstance(result, WebhookResult)

        # Failure (bad signature)
        raw_body = json.dumps(payload).encode("utf-8")
        bad_request = WebhookRequest(
            raw_body=raw_body,
            signature="aa" * 64,
            timestamp="123",
        )
        result = handle_webhook(bad_request, public_key)
        assert isinstance(result, WebhookResult)

    def test_unicode_command_option(self, private_key: str, public_key: str) -> None:
        """Korean text in command options should be preserved."""
        payload = _make_slash_payload(
            command_name="meeting",
            options=[
                {"name": "agenda", "type": 3, "value": "뮤직비디오 오프닝 아이디어 회의"},
            ],
        )
        request = _make_webhook_request(payload, private_key)

        result = handle_webhook(request, public_key)
        assert result.parsed_interaction is not None
        assert result.parsed_interaction.command_options["agenda"] == "뮤직비디오 오프닝 아이디어 회의"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Error types
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorTypes:
    """Tests for the structured error hierarchy."""

    def test_webhook_handler_error_has_code(self) -> None:
        exc = WebhookHandlerError("msg")
        assert exc.code == "WEBHOOK_ERROR"
        assert str(exc) == "msg"

    def test_signature_verification_error_has_code(self) -> None:
        exc = SignatureVerificationError()
        assert exc.code == "SIGNATURE_VERIFICATION_FAILED"

    def test_invalid_payload_error_has_code(self) -> None:
        exc = InvalidPayloadError()
        assert exc.code == "INVALID_PAYLOAD"

    def test_custom_message_preserved(self) -> None:
        exc = SignatureVerificationError("custom message")
        assert "custom message" in str(exc)
        assert exc.code == "SIGNATURE_VERIFICATION_FAILED"


# ═══════════════════════════════════════════════════════════════════════════
# 9. ParsedInteraction edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestParsedInteractionEdgeCases:
    """Edge-case tests for ParsedInteraction behaviour."""

    def test_default_values(self) -> None:
        p = ParsedInteraction(
            interaction_id="i1",
            interaction_token="t1",
            type=DiscordInteractionType.APPLICATION_COMMAND,
            user_id="u1",
            channel_id="c1",
        )
        assert p.guild_id == ""
        assert p.command_name == ""
        assert p.command_options == {}
        assert p.raw_payload == {}

    def test_frozen_dataclass(self) -> None:
        p = ParsedInteraction(
            interaction_id="i1",
            interaction_token="t1",
            type=DiscordInteractionType.PING,
            user_id="u1",
            channel_id="c1",
        )
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            p.user_id = "new"  # type: ignore[misc]
