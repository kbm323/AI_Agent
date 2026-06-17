"""Tests for Handler Routing Module (Sub-AC 1.1.3).

Covers the handler routing module that inspects validated interaction
payloads, extracts command type and subcommand, and routes to the
appropriate handler (PING responder, meeting request parser, or
unknown-command responder).  All tests use mock interaction payloads
and handler stubs — no network, no signature keys, no Discord
application registration required.

Test categories
---------------
1.  PING routing — type 1 interactions route to PING handler
2.  Registered command routing — registered commands route to their handler
3.  Unregistered command routing — unregistered commands route to unknown
4.  Unknown interaction types — type 3, 4, 5 route to unknown handler
5.  Payload normalisation — dict, str, bytes inputs
6.  Envelope validation — missing id, token, type fields
7.  Edge cases — empty data, missing name, None values, unicode
8.  HandlerRegistry — register, get, introspection, len, contains
9.  Default handlers — PING returns PONG, unknown returns error message
10. create_default_registry — factory function with built-in handlers
11. RouteResult invariants — success/error mutual exclusion
12. HandlerRoute invariants — all fields populated correctly
13. No-registry mode — routes resolve without handler callables
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.handler_router import (
    DiscordInteractionType,
    HandlerRegistry,
    HandlerRoute,
    InteractionHandler,
    RouteResult,
    _default_ping_handler,  # noqa: PLC2701 — internal exposed for testing
    _default_unknown_handler,  # noqa: PLC2701 — internal exposed for testing
    create_default_registry,
    route_interaction,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def registry() -> HandlerRegistry:
    """A fresh, empty HandlerRegistry (no pre-registered handlers)."""
    return HandlerRegistry()


@pytest.fixture
def default_registry() -> HandlerRegistry:
    """A registry with built-in PING and unknown handlers."""
    return create_default_registry()


@pytest.fixture
def mock_handler() -> InteractionHandler:
    """A mock handler that returns a success response."""

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {"type": 4, "data": {"content": "handler invoked"}}

    return handler


@pytest.fixture
def spy_handler() -> tuple[InteractionHandler, list[dict[str, Any]]]:
    """A spy handler that records invocations.

    Returns a tuple of (handler_callable, invocation_list).
    """
    invocations: list[dict[str, Any]] = []

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        invocations.append(payload)
        return {"type": 4, "data": {"content": "spy invoked"}}

    return handler, invocations


@pytest.fixture
def ping_payload() -> dict[str, Any]:
    """A realistic Discord PING interaction payload."""
    return {"type": 1, "id": "interaction_ping_001", "token": "tok_ping_abc123", "version": 1}


@pytest.fixture
def meeting_payload() -> dict[str, Any]:
    """A realistic Discord APPLICATION_COMMAND meeting payload."""
    return {
        "type": 2,
        "id": "interaction_cmd_001",
        "token": "tok_cmd_abc123",
        "version": 1,
        "channel_id": "channel_456",
        "data": {
            "id": "cmd_001",
            "name": "meeting",
            "type": 1,
            "options": [{"name": "agenda", "type": 3, "value": "Design Review"}],
        },
    }


@pytest.fixture
def unknown_command_payload() -> dict[str, Any]:
    """A payload with an unrecognised command name."""
    return {
        "type": 2,
        "id": "interaction_cmd_002",
        "token": "tok_cmd_def456",
        "version": 1,
        "data": {"id": "cmd_002", "name": "play_music", "type": 1},
    }


@pytest.fixture
def missing_data_payload() -> dict[str, Any]:
    """An APPLICATION_COMMAND payload with no data object."""
    return {"type": 2, "id": "interaction_cmd_003", "token": "tok_cmd_ghi789", "version": 1}


# ═══════════════════════════════════════════════════════════════════════════
# 1. PING routing
# ═══════════════════════════════════════════════════════════════════════════


class TestPingRouting:
    """PING (type 1) interactions route to the PING handler."""

    def test_ping_with_default_handler(self, default_registry, ping_payload):
        """PING with default registry routes to 'ping' with built-in handler."""
        result = route_interaction(ping_payload, registry=default_registry)

        assert result.success is True
        assert result.route is not None
        assert result.route.handler_key == "ping"
        assert result.route.interaction_type == DiscordInteractionType.PING
        assert result.route.command_name == ""
        assert result.route.handler is not None

    def test_ping_handler_returns_pong(self, default_registry, ping_payload):
        """The built-in PING handler returns Discord PONG (type 1)."""
        result = route_interaction(ping_payload, registry=default_registry)
        handler = result.route.handler
        assert handler is not None
        response = handler(ping_payload)
        assert response == {"type": 1}

    def test_ping_with_custom_handler(self, registry, ping_payload, mock_handler):
        """Custom PING handler is invoked instead of the built-in."""
        registry.register_ping(mock_handler)
        result = route_interaction(ping_payload, registry=registry)

        assert result.success is True
        assert result.route.handler_key == "ping"
        assert result.route.handler is mock_handler
        response = result.route.handler(ping_payload)
        assert response == {"type": 4, "data": {"content": "handler invoked"}}

    def test_ping_without_registry(self, ping_payload):
        """PING routes correctly even without a registry (no handler attached)."""
        result = route_interaction(ping_payload)

        assert result.success is True
        assert result.route.handler_key == "ping"
        assert result.route.handler is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Registered command routing
# ═══════════════════════════════════════════════════════════════════════════


class TestRegisteredCommandRouting:
    """Registered APPLICATION_COMMAND interactions route to their handler."""

    def test_meeting_routes_to_meeting_handler(
        self, default_registry, meeting_payload, mock_handler
    ):
        """Meeting command routes to the registered meeting handler."""
        default_registry.register("meeting", mock_handler)
        result = route_interaction(meeting_payload, registry=default_registry)

        assert result.success is True
        assert result.route.handler_key == "meeting"
        assert result.route.command_name == "meeting"
        assert result.route.interaction_type == DiscordInteractionType.APPLICATION_COMMAND
        assert result.route.handler is mock_handler

    def test_multiple_registered_commands(self, default_registry, mock_handler):
        """Multiple commands can be registered and routed independently."""
        default_registry.register("meeting", mock_handler)

        def status_handler(p):
            return {"type": 4, "data": {"content": "status ok"}}

        default_registry.register("status", status_handler)

        # Route meeting
        meeting_payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {"name": "meeting", "type": 1},
        }
        result = route_interaction(meeting_payload, registry=default_registry)
        assert result.route.handler_key == "meeting"
        assert result.route.handler is mock_handler

        # Route status
        status_payload = {
            "type": 2,
            "id": "def",
            "token": "tok",
            "data": {"name": "status", "type": 1},
        }
        result = route_interaction(status_payload, registry=default_registry)
        assert result.route.handler_key == "status"
        assert result.route.handler is status_handler

    def test_handler_receives_full_payload(self, registry, meeting_payload, spy_handler):
        """The handler receives the complete interaction payload."""
        spy, invocations = spy_handler
        registry.register("meeting", spy)
        result = route_interaction(meeting_payload, registry=registry)

        assert result.success
        assert result.route is not None
        result.route.handler(meeting_payload)
        assert len(invocations) == 1
        assert invocations[0] == meeting_payload

    def test_command_name_case_sensitive(self, registry, mock_handler):
        """Command name lookups are case-sensitive."""
        registry.register("Meeting", mock_handler)
        registry.register("meeting", mock_handler)

        # Lowercase lookup
        payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {"name": "meeting", "type": 1},
        }
        result = route_interaction(payload, registry=registry)
        assert result.route.handler_key == "meeting"

        # Uppercase lookup
        payload["data"]["name"] = "Meeting"
        result = route_interaction(payload, registry=registry)
        assert result.route.handler_key == "Meeting"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Unregistered command routing
# ═══════════════════════════════════════════════════════════════════════════


class TestUnregisteredCommandRouting:
    """Unregistered commands and unknown interaction types route to the
    unknown-command handler."""

    def test_unregistered_command_routes_to_unknown(
        self, default_registry, unknown_command_payload
    ):
        """A command with no registered handler routes to 'unknown'."""
        result = route_interaction(unknown_command_payload, registry=default_registry)

        assert result.success is True
        assert result.route.handler_key == "unknown"
        assert result.route.command_name == "play_music"
        assert result.route.interaction_type == DiscordInteractionType.APPLICATION_COMMAND

    def test_unknown_handler_returns_error_message(
        self, default_registry, unknown_command_payload
    ):
        """The built-in unknown handler returns a user-facing error message."""
        result = route_interaction(unknown_command_payload, registry=default_registry)
        handler = result.route.handler
        assert handler is not None
        response = handler(unknown_command_payload)

        assert response["type"] == 4
        assert "Unknown command" in response["data"]["content"]
        assert "play_music" in response["data"]["content"]

    def test_unknown_handler_when_no_registry(self, unknown_command_payload):
        """Without a registry, unknown commands resolve with no handler."""
        result = route_interaction(unknown_command_payload)

        assert result.success is True
        assert result.route.handler_key == "unknown"
        assert result.route.handler is None

    def test_unknown_handler_with_no_data(self, default_registry, missing_data_payload):
        """APPLICATION_COMMAND without data object returns an error, not unknown route."""
        result = route_interaction(missing_data_payload, registry=default_registry)

        assert result.success is False
        assert result.error_code == "MISSING_DATA"

    def test_unknown_command_name_empty(
        self, default_registry
    ):
        """APPLICATION_COMMAND with empty command name returns an error."""
        payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {"name": "", "type": 1},
        }
        result = route_interaction(payload, registry=default_registry)

        assert result.success is False
        assert result.error_code == "MISSING_COMMAND_NAME"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Unknown interaction types (future support)
# ═══════════════════════════════════════════════════════════════════════════


class TestUnknownInteractionTypes:
    """Interaction types 3 (MESSAGE_COMPONENT), 4 (AUTOCOMPLETE), and
    5 (MODAL_SUBMIT) route to the unknown-command handler."""

    @pytest.mark.parametrize(
        "interaction_type,expected_enum",
        [
            (3, DiscordInteractionType.MESSAGE_COMPONENT),
            (4, DiscordInteractionType.APPLICATION_COMMAND_AUTOCOMPLETE),
            (5, DiscordInteractionType.MODAL_SUBMIT),
        ],
    )
    def test_non_command_types_route_to_unknown(
        self, default_registry, interaction_type, expected_enum
    ):
        """Non-APPLICATION_COMMAND types route to unknown handler."""
        payload = {"type": interaction_type, "id": "abc", "token": "tok"}
        result = route_interaction(payload, registry=default_registry)

        assert result.success is True
        assert result.route.handler_key == "unknown"
        assert result.route.interaction_type == expected_enum
        assert result.route.command_name == ""

    def test_message_component_with_unknown_handler(
        self, default_registry, mock_handler
    ):
        """MESSAGE_COMPONENT uses unknown handler when registered."""
        default_registry.register_unknown(mock_handler)
        payload = {"type": 3, "id": "abc", "token": "tok"}
        result = route_interaction(payload, registry=default_registry)

        assert result.success
        assert result.route.handler is mock_handler
        response = result.route.handler(payload)
        assert response["data"]["content"] == "handler invoked"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Payload normalisation
# ═══════════════════════════════════════════════════════════════════════════


class TestPayloadNormalisation:
    """The router accepts dict, str, and bytes payloads."""

    def test_dict_payload(self, default_registry, ping_payload):
        """Dict payloads are accepted directly."""
        result = route_interaction(ping_payload, registry=default_registry)
        assert result.success

    def test_str_payload(self, default_registry):
        """JSON string payloads are parsed."""
        payload_str = '{"type":1,"id":"abc","token":"tok"}'
        result = route_interaction(payload_str, registry=default_registry)
        assert result.success
        assert result.route.handler_key == "ping"

    def test_bytes_payload(self, default_registry):
        """UTF-8 bytes payloads are decoded and parsed."""
        payload_bytes = b'{"type":1,"id":"abc","token":"tok"}'
        result = route_interaction(payload_bytes, registry=default_registry)
        assert result.success
        assert result.route.handler_key == "ping"

    def test_invalid_utf8(self):
        """Non-UTF-8 bytes payloads fail gracefully."""
        result = route_interaction(b"\xff\xfe\x00\x01")
        assert result.success is False
        assert result.error_code == "INVALID_UTF8"

    def test_invalid_json(self):
        """Malformed JSON strings fail gracefully."""
        result = route_interaction("{not valid json}")
        assert result.success is False
        assert result.error_code == "INVALID_JSON"

    def test_json_array_rejected(self):
        """JSON arrays (not objects) are rejected."""
        result = route_interaction("[1, 2, 3]")
        assert result.success is False
        assert result.error_code == "NOT_A_DICT"

    def test_non_dict_python_object(self):
        """Passing a list (not a dict) as the payload fails."""
        result = route_interaction([1, 2, 3])  # type: ignore
        assert result.success is False
        assert result.error_code == "NOT_A_DICT"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Envelope validation
# ═══════════════════════════════════════════════════════════════════════════


class TestEnvelopeValidation:
    """Missing or invalid envelope fields produce structured errors."""

    def test_missing_id(self):
        """Payload without 'id' fails with MISSING_ID."""
        result = route_interaction({"type": 1, "token": "tok"})
        assert result.success is False
        assert result.error_code == "MISSING_ID"

    def test_empty_id(self):
        """Payload with empty string 'id' fails with MISSING_ID."""
        result = route_interaction({"type": 1, "id": "", "token": "tok"})
        assert result.success is False
        assert result.error_code == "MISSING_ID"

    def test_missing_token(self):
        """Payload without 'token' fails with MISSING_TOKEN."""
        result = route_interaction({"type": 1, "id": "abc"})
        assert result.success is False
        assert result.error_code == "MISSING_TOKEN"

    def test_empty_token(self):
        """Payload with empty string 'token' fails with MISSING_TOKEN."""
        result = route_interaction({"type": 1, "id": "abc", "token": ""})
        assert result.success is False
        assert result.error_code == "MISSING_TOKEN"

    def test_missing_type(self):
        """Payload without 'type' fails with MISSING_TYPE."""
        result = route_interaction({"id": "abc", "token": "tok"})
        assert result.success is False
        assert result.error_code == "MISSING_TYPE"

    def test_type_is_string(self):
        """'type' as a string (not integer) fails with INVALID_TYPE."""
        result = route_interaction({"type": "2", "id": "abc", "token": "tok"})
        assert result.success is False
        assert result.error_code == "INVALID_TYPE"

    def test_type_is_none(self):
        """'type' as None fails."""
        result = route_interaction({"type": None, "id": "abc", "token": "tok"})
        assert result.success is False
        assert result.error_code == "MISSING_TYPE"

    def test_unknown_interaction_type(self):
        """Type 99 (not in DiscordInteractionType) fails."""
        result = route_interaction({"type": 99, "id": "abc", "token": "tok"})
        assert result.success is False
        assert result.error_code == "UNKNOWN_INTERACTION_TYPE"

    def test_type_zero(self):
        """Type 0 is not a valid Discord interaction type."""
        result = route_interaction({"type": 0, "id": "abc", "token": "tok"})
        assert result.success is False
        assert result.error_code == "UNKNOWN_INTERACTION_TYPE"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for payload content and routing behaviour."""

    def test_korean_command_name(self, default_registry):
        """Korean command names work correctly."""
        payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {"name": "회의", "type": 1},
        }
        result = route_interaction(payload, registry=default_registry)
        assert result.success
        assert result.route.handler_key == "unknown"
        assert result.route.command_name == "회의"

    def test_unicode_agenda_in_payload(self, default_registry, mock_handler):
        """Unicode characters in the payload are preserved."""
        default_registry.register("meeting", mock_handler)
        payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {
                "name": "meeting",
                "type": 1,
                "options": [{"name": "agenda", "type": 3, "value": "뮤직비디오 오프닝 아이디어 회의"}],
            },
        }
        result = route_interaction(payload, registry=default_registry)
        assert result.success
        assert result.route.handler_key == "meeting"

    def test_large_payload(self, default_registry):
        """Very large payloads are handled correctly."""
        large_options = [
            {"name": f"option_{i}", "type": 3, "value": f"value_{i}"}
            for i in range(100)
        ]
        payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {"name": "meeting", "type": 1, "options": large_options},
        }
        result = route_interaction(payload, registry=default_registry)
        assert result.success
        assert result.route.handler_key == "unknown"  # meeting not registered

    def test_deeply_nested_payload(self, default_registry):
        """Deeply nested payload is handled gracefully."""
        payload = {"type": 1, "id": "abc", "token": "tok", "nested": {"a": {"b": {"c": [1, 2, 3]}}}}
        result = route_interaction(payload, registry=default_registry)
        assert result.success
        assert result.route.handler_key == "ping"

    def test_empty_payload_dict(self):
        """Completely empty dict fails."""
        result = route_interaction({})
        assert result.success is False

    def test_command_name_is_none(self, default_registry):
        """Command name as None in data.name."""
        payload = {
            "type": 2,
            "id": "abc",
            "token": "tok",
            "data": {"name": None, "type": 1},
        }
        result = route_interaction(payload, registry=default_registry)
        assert result.success is False
        assert result.error_code == "MISSING_COMMAND_NAME"

    def test_data_is_string_not_dict(self, default_registry):
        """data field is a string instead of a dict."""
        payload = {"type": 2, "id": "abc", "token": "tok", "data": "not_a_dict"}
        result = route_interaction(payload, registry=default_registry)
        assert result.success is False
        assert result.error_code == "MISSING_DATA"


# ═══════════════════════════════════════════════════════════════════════════
# 8. HandlerRegistry
# ═══════════════════════════════════════════════════════════════════════════


class TestHandlerRegistry:
    """HandlerRegistry registration, lookup, and introspection."""

    def test_register_and_get(self, registry, mock_handler):
        """Registering a handler makes it retrievable by command name."""
        registry.register("meeting", mock_handler)
        assert registry.get("meeting") is mock_handler

    def test_get_unregistered_returns_none(self, registry):
        """Looking up an unregistered command returns None."""
        assert registry.get("nonexistent") is None

    def test_register_overwrites(self, registry, mock_handler):
        """Registering the same command name overwrites the previous handler."""

        def other_handler(p):
            return {"type": 4, "data": {"content": "other"}}

        registry.register("meeting", mock_handler)
        registry.register("meeting", other_handler)
        assert registry.get("meeting") is other_handler
        assert registry.get("meeting") is not mock_handler

    def test_register_empty_string_raises(self, registry, mock_handler):
        """Registering with an empty command name raises TypeError."""
        with pytest.raises(TypeError, match="command_name must be a non-empty string"):
            registry.register("", mock_handler)

    def test_register_whitespace_raises(self, registry, mock_handler):
        """Registering with whitespace-only command name raises TypeError."""
        with pytest.raises(TypeError, match="command_name must be a non-empty string"):
            registry.register("   ", mock_handler)

    def test_len(self, registry, mock_handler):
        """len() returns the number of registered command handlers."""
        assert len(registry) == 0
        registry.register("meeting", mock_handler)
        assert len(registry) == 1
        registry.register("status", mock_handler)
        assert len(registry) == 2

    def test_contains(self, registry, mock_handler):
        """The 'in' operator checks for registered command names."""
        registry.register("meeting", mock_handler)
        assert "meeting" in registry
        assert "status" not in registry

    def test_registered_commands(self, registry, mock_handler):
        """registered_commands returns an immutable set of command names."""
        registry.register("meeting", mock_handler)
        registry.register("status", mock_handler)
        commands = registry.registered_commands
        assert commands == frozenset({"meeting", "status"})
        assert isinstance(commands, frozenset)

    def test_ping_handler(self, registry, mock_handler):
        """PING handler can be registered and retrieved."""
        assert registry.get_ping() is None
        assert registry.has_ping_handler is False
        registry.register_ping(mock_handler)
        assert registry.get_ping() is mock_handler
        assert registry.has_ping_handler is True

    def test_unknown_handler(self, registry, mock_handler):
        """Unknown-command handler can be registered and retrieved."""
        assert registry.get_unknown() is None
        assert registry.has_unknown_handler is False
        registry.register_unknown(mock_handler)
        assert registry.get_unknown() is mock_handler
        assert registry.has_unknown_handler is True

    def test_ping_handler_not_counted_in_len(self, registry, mock_handler):
        """PING handler is not counted in len() (only command handlers)."""
        registry.register_ping(mock_handler)
        registry.register_unknown(mock_handler)
        assert len(registry) == 0
        registry.register("meeting", mock_handler)
        assert len(registry) == 1

    def test_register_unknown_not_counted_in_contains(self, registry, mock_handler):
        """Unknown handler command key is not in 'in' check."""
        registry.register_unknown(mock_handler)
        assert "unknown" not in registry


# ═══════════════════════════════════════════════════════════════════════════
# 9. Default handlers
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultHandlers:
    """Built-in PING and unknown-command handlers."""

    def test_ping_handler(self):
        """_default_ping_handler returns PONG response."""
        response = _default_ping_handler({"type": 1})
        assert response == {"type": 1}

    def test_ping_handler_ignores_other_fields(self):
        """PING handler returns only type:1 regardless of input fields."""
        response = _default_ping_handler({"type": 1, "id": "xyz", "token": "abc", "extra": "data"})
        assert response == {"type": 1}

    def test_unknown_handler_shows_command_name(self):
        """Unknown handler includes the command name in the error message."""
        payload = {"type": 2, "data": {"name": "play_music"}}
        response = _default_unknown_handler(payload)
        assert response["type"] == 4
        assert "/play_music" in response["data"]["content"]

    def test_unknown_handler_without_data(self):
        """Unknown handler gracefully handles payloads without data."""
        payload = {"type": 2}
        response = _default_unknown_handler(payload)
        assert response["type"] == 4
        assert "unknown" in response["data"]["content"]


# ═══════════════════════════════════════════════════════════════════════════
# 10. create_default_registry
# ═══════════════════════════════════════════════════════════════════════════


class TestCreateDefaultRegistry:
    """The create_default_registry() factory function."""

    def test_has_ping_handler(self):
        """Default registry has a PING handler."""
        reg = create_default_registry()
        assert reg.has_ping_handler
        assert reg.get_ping() is not None

    def test_has_unknown_handler(self):
        """Default registry has an unknown-command handler."""
        reg = create_default_registry()
        assert reg.has_unknown_handler
        assert reg.get_unknown() is not None

    def test_empty_command_handlers(self):
        """Default registry starts with zero command handlers."""
        reg = create_default_registry()
        assert len(reg) == 0
        assert reg.registered_commands == frozenset()

    def test_ping_handler_returns_pong(self):
        """Default PING handler returns PONG."""
        reg = create_default_registry()
        response = reg.get_ping()({"type": 1})
        assert response == {"type": 1}

    def test_unknown_handler_returns_error(self):
        """Default unknown handler returns an error message."""
        reg = create_default_registry()
        response = reg.get_unknown()({"type": 2, "data": {"name": "test"}})
        assert response["type"] == 4
        assert "Unknown command" in response["data"]["content"]

    def test_each_call_returns_new_registry(self):
        """Each call creates an independent, fresh registry."""
        reg1 = create_default_registry()
        reg2 = create_default_registry()

        def mock(p):
            return {"type": 4, "data": {"content": "ok"}}

        reg1.register("meeting", mock)
        assert "meeting" in reg1
        assert "meeting" not in reg2


# ═══════════════════════════════════════════════════════════════════════════
# 11. RouteResult invariants
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteResultInvariants:
    """Structural invariants of RouteResult."""

    def test_success_has_route(self, default_registry, ping_payload):
        """When success=True, route is always populated."""
        result = route_interaction(ping_payload, registry=default_registry)
        assert result.success is True
        assert result.route is not None
        assert result.error == ""
        assert result.error_code == ""

    def test_failure_has_no_route(self):
        """When success=False, route is None and error is populated."""
        result = route_interaction({"id": "abc", "token": "tok"})
        assert result.success is False
        assert result.route is None
        assert result.error != ""
        assert result.error_code != ""

    def test_route_result_is_immutable(self, ping_payload):
        """RouteResult is frozen (immutable)."""
        result = route_interaction(ping_payload)
        with pytest.raises(Exception):
            result.success = False  # type: ignore

    def test_route_result_repr(self, default_registry, ping_payload):
        """RouteResult has a meaningful repr."""
        result = route_interaction(ping_payload, registry=default_registry)
        rep = repr(result)
        assert "RouteResult" in rep
        assert "success=True" in rep


# ═══════════════════════════════════════════════════════════════════════════
# 12. HandlerRoute invariants
# ═══════════════════════════════════════════════════════════════════════════


class TestHandlerRouteInvariants:
    """Structural invariants of HandlerRoute."""

    def test_ping_route_fields(self, default_registry, ping_payload):
        """PING route has correct field values."""
        result = route_interaction(ping_payload, registry=default_registry)
        route = result.route
        assert route.interaction_type == DiscordInteractionType.PING
        assert route.command_name == ""
        assert route.handler_key == "ping"
        assert route.handler is not None

    def test_command_route_fields(self, default_registry, meeting_payload, mock_handler):
        """Command route has correct field values."""
        default_registry.register("meeting", mock_handler)
        result = route_interaction(meeting_payload, registry=default_registry)
        route = result.route
        assert route.interaction_type == DiscordInteractionType.APPLICATION_COMMAND
        assert route.command_name == "meeting"
        assert route.handler_key == "meeting"
        assert route.handler is mock_handler

    def test_raw_payload_preserved(self, default_registry, ping_payload):
        """The raw payload dict is preserved in the route."""
        result = route_interaction(ping_payload, registry=default_registry)
        assert result.route.raw_payload is ping_payload

    def test_handler_route_is_immutable(self, default_registry, ping_payload):
        """HandlerRoute is frozen (immutable)."""
        result = route_interaction(ping_payload, registry=default_registry)
        route = result.route
        with pytest.raises(Exception):
            route.handler_key = "other"  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════
# 13. No-registry mode
# ═══════════════════════════════════════════════════════════════════════════


class TestNoRegistryMode:
    """Routing without a registry (handler=None in all routes)."""

    def test_ping_without_registry_has_no_handler(self, ping_payload):
        """PING route has no handler when registry is None."""
        result = route_interaction(ping_payload)
        assert result.success
        assert result.route.handler_key == "ping"
        assert result.route.handler is None

    def test_command_without_registry_has_no_handler(self, meeting_payload):
        """Command route has no handler when registry is None."""
        result = route_interaction(meeting_payload)
        assert result.success
        assert result.route.handler_key == "unknown"
        assert result.route.handler is None

    def test_message_component_without_registry(self):
        """MESSAGE_COMPONENT route has no handler when registry is None."""
        result = route_interaction({"type": 3, "id": "abc", "token": "tok"})
        assert result.success
        assert result.route.handler_key == "unknown"
        assert result.route.handler is None
