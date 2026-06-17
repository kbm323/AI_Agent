"""Tests for Discord Interaction Payload Parser (Sub-AC 1b-i).

Covers the focused payload parser that receives raw Discord interaction
JSON, verifies type is APPLICATION_COMMAND and command name matches,
and extracts raw option values into a flat name→value dict.

Test categories
---------------
1. Happy path — APPLICATION_COMMAND with options
2. Command name matching — expected name, mismatch, no expectation
3. Interaction type validation — PING rejection, unknown type rejection
4. Option extraction — flat options, subcommands, subcommand groups
5. Option type handling — string, integer, boolean, missing value
6. Payload envelope validation — missing id, token, type
7. Input formats — dict, JSON string, UTF-8 bytes
8. Error conditions — invalid JSON, non-dict, missing data object
9. Edge cases — empty options, no expected command, Korean text
"""

from __future__ import annotations

import json

import pytest

from src.discord_interaction_parser import (
    DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
    DISCORD_INTERACTION_TYPE_PING,
    DISCORD_OPTION_TYPE_SUB_COMMAND,
    DISCORD_OPTION_TYPE_SUB_COMMAND_GROUP,
    InteractionParseResult,
    ParsedCommandOptions,
    _flatten_options,
    _validate_envelope,
    parse_interaction_command,
    parse_interaction_command_from_bytes,
)


# ── Test payload builders ────────────────────────────────────────────────


def _make_payload(
    *,
    command_name: str = "meeting",
    options: list[dict] | None = None,
    interaction_id: str = "interaction-001",
    token: str = "tok-abc123",
    interaction_type: int = 2,
    guild_id: str = "guild-789",
    channel_id: str = "channel-456",
    user_id: str = "user-111",
    user_name: str = "testuser",
) -> dict:
    """Build a complete, valid Discord APPLICATION_COMMAND interaction payload.

    All fields are pre-populated with sensible defaults so individual
    tests only need to override what they are specifically testing.
    """
    payload: dict = {
        "id": interaction_id,
        "application_id": "app-999",
        "type": interaction_type,
        "token": token,
        "version": 1,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "member": {"user": {"id": user_id, "username": user_name}},
        "data": {
            "id": "cmd-data-001",
            "name": command_name,
            "type": 1,
        },
    }
    if options is not None:
        payload["data"]["options"] = options
    return payload


def _make_ping_payload() -> dict:
    """Build a minimal Discord PING interaction payload."""
    return {
        "id": "ping-001",
        "token": "tok-ping",
        "type": DISCORD_INTERACTION_TYPE_PING,
        "application_id": "app-999",
        "version": 1,
    }


def _make_payload_json(**kwargs) -> str:
    """Build a payload and serialize to a JSON string."""
    return json.dumps(_make_payload(**kwargs), ensure_ascii=False)


def _make_payload_bytes(**kwargs) -> bytes:
    """Build a payload and serialize to UTF-8 bytes."""
    return json.dumps(_make_payload(**kwargs), ensure_ascii=False).encode("utf-8")


# ── Helper assertions ────────────────────────────────────────────────────


def assert_success(result: InteractionParseResult) -> ParsedCommandOptions:
    """Assert the result is a success and return the parsed options."""
    assert result.success is True, f"Expected success, got error: {result.error}"
    assert result.parsed is not None
    return result.parsed


def assert_failure(
    result: InteractionParseResult,
    *,
    error_code: str,
    error_substring: str | None = None,
) -> None:
    """Assert the result is a failure with the expected error code."""
    assert result.success is False
    assert result.error_code == error_code, (
        f"Expected error_code '{error_code}', got '{result.error_code}': {result.error}"
    )
    if error_substring is not None:
        assert error_substring.lower() in result.error.lower(), (
            f"Expected error to contain '{error_substring}', got: {result.error}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path — APPLICATION_COMMAND with options
# ═══════════════════════════════════════════════════════════════════════════


class TestParseApplicationCommandHappyPath:
    """Tests for successful APPLICATION_COMMAND parsing."""

    def test_parses_simple_slash_command_with_options(self) -> None:
        """Parse a basic /meeting agenda:\"Hello\" command."""
        payload = _make_payload(
            command_name="meeting",
            options=[{"name": "agenda", "type": 3, "value": "Design Review"}],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)

        assert parsed.command_name == "meeting"
        assert parsed.options == {"agenda": "Design Review"}

    def test_extracts_multiple_options(self) -> None:
        """Multiple options should all appear in the flat dict."""
        payload = _make_payload(
            command_name="meeting",
            options=[
                {"name": "agenda", "type": 3, "value": "Q2 Budget"},
                {"name": "priority", "type": 3, "value": "P1"},
                {"name": "rounds", "type": 4, "value": 3},
                {"name": "urgent", "type": 5, "value": True},
            ],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)

        assert parsed.command_name == "meeting"
        assert parsed.options["agenda"] == "Q2 Budget"
        assert parsed.options["priority"] == "P1"
        assert parsed.options["rounds"] == 3
        assert parsed.options["urgent"] is True
        assert len(parsed.options) == 4

    def test_extracts_options_from_dict_input(self) -> None:
        """Direct dict input should work without JSON round-trip."""
        payload = _make_payload(
            options=[{"name": "topic", "type": 3, "value": "Server migration"}],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.options["topic"] == "Server migration"

    def test_extracts_options_from_json_string(self) -> None:
        """JSON string input should be parsed transparently."""
        json_str = _make_payload_json(
            options=[{"name": "topic", "type": 3, "value": "API Review"}],
        )
        result = parse_interaction_command(json_str)
        parsed = assert_success(result)
        assert parsed.options["topic"] == "API Review"

    def test_extracts_options_from_json_bytes(self) -> None:
        """UTF-8 bytes input should be parsed transparently."""
        json_bytes = _make_payload_bytes(
            options=[{"name": "topic", "type": 3, "value": "Deploy pipeline"}],
        )
        result = parse_interaction_command(json_bytes)
        parsed = assert_success(result)
        assert parsed.options["topic"] == "Deploy pipeline"

    def test_parse_from_bytes_convenience_wrapper(self) -> None:
        """parse_interaction_command_from_bytes works identically."""
        json_bytes = _make_payload_bytes(
            options=[{"name": "agenda", "type": 3, "value": "Hello"}],
        )
        result = parse_interaction_command_from_bytes(json_bytes)
        parsed = assert_success(result)
        assert parsed.options["agenda"] == "Hello"

    def test_options_without_value_return_none(self) -> None:
        """An option with no 'value' key should yield None."""
        payload = _make_payload(
            options=[{"name": "flag", "type": 5}],  # no 'value' key
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.options["flag"] is None

    def test_empty_options_list_is_valid(self) -> None:
        """A command with no options should parse with empty dict."""
        payload = _make_payload(command_name="status", options=[])
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.command_name == "status"
        assert parsed.options == {}

    def test_missing_options_key_is_valid(self) -> None:
        """A command data object without 'options' key should parse fine."""
        payload = _make_payload(command_name="ping", options=[])
        # Remove the 'options' key entirely to simulate Discord omitting it
        del payload["data"]["options"]
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.command_name == "ping"
        assert parsed.options == {}

    def test_stores_raw_payload_in_result(self) -> None:
        """The original payload dict should be stored for debugging."""
        payload = _make_payload(
            options=[{"name": "x", "type": 3, "value": "y"}],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.raw_payload is payload  # identity, not copy

    def test_korean_text_in_option_values(self) -> None:
        """Korean (non-ASCII) option values should be preserved exactly."""
        payload = _make_payload(
            options=[
                {"name": "agenda", "type": 3, "value": "뮤직비디오 오프닝 아이디어"},
                {"name": "team", "type": 3, "value": "아트팀"},
            ],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.options["agenda"] == "뮤직비디오 오프닝 아이디어"
        assert parsed.options["team"] == "아트팀"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Command name matching
# ═══════════════════════════════════════════════════════════════════════════


class TestCommandNameMatching:
    """Tests for expected_command_name validation."""

    def test_passes_when_command_name_matches_expected(self) -> None:
        payload = _make_payload(command_name="meeting")
        result = parse_interaction_command(
            payload,
            expected_command_name="meeting",
        )
        parsed = assert_success(result)
        assert parsed.command_name == "meeting"

    def test_rejects_when_command_name_mismatches(self) -> None:
        payload = _make_payload(command_name="status")
        result = parse_interaction_command(
            payload,
            expected_command_name="meeting",
        )
        assert_failure(result, error_code="COMMAND_NAME_MISMATCH")
        assert "'/meeting'" in result.error
        assert "'/status'" in result.error

    def test_accepts_any_command_if_expected_not_specified(self) -> None:
        """When expected_command_name is None, any command name is valid."""
        payload = _make_payload(command_name="arbitrary-command")
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.command_name == "arbitrary-command"

    def test_expected_none_explicit(self) -> None:
        """Explicit expected_command_name=None should accept any name."""
        payload = _make_payload(command_name="cancel")
        result = parse_interaction_command(
            payload,
            expected_command_name=None,
        )
        parsed = assert_success(result)
        assert parsed.command_name == "cancel"

    def test_command_name_case_sensitive(self) -> None:
        """Command name matching is case-sensitive (Discord uses lowercase)."""
        payload = _make_payload(command_name="Meeting")  # uppercase M
        result = parse_interaction_command(
            payload,
            expected_command_name="meeting",
        )
        assert_failure(result, error_code="COMMAND_NAME_MISMATCH")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Interaction type validation
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractionTypeValidation:
    """Tests for interaction type verification."""

    def test_rejects_ping_interaction(self) -> None:
        """PING (type 1) interactions should be rejected."""
        payload = _make_ping_payload()
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="NOT_APPLICATION_COMMAND")

    def test_rejects_unknown_interaction_type(self) -> None:
        """An interaction type outside 1-2 should be rejected."""
        payload = _make_payload(interaction_type=99)
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="UNKNOWN_TYPE")

    def test_rejects_type_zero(self) -> None:
        payload = _make_payload(interaction_type=0)
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="UNKNOWN_TYPE")

    def test_rejects_type_as_string(self) -> None:
        """type must be an integer, not a string like \"2\"."""
        payload = {
            "id": "abc",
            "token": "tok",
            "type": "2",  # string, not int
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_TYPE")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Option extraction — subcommands and nesting
# ═══════════════════════════════════════════════════════════════════════════


class TestOptionFlattening:
    """Tests for _flatten_options with nested subcommands."""

    def test_flat_options_single(self) -> None:
        result = _flatten_options([
            {"name": "agenda", "type": 3, "value": "Hello"},
        ])
        assert result == {"agenda": "Hello"}

    def test_flat_options_multiple(self) -> None:
        result = _flatten_options([
            {"name": "a", "type": 3, "value": "1"},
            {"name": "b", "type": 4, "value": 42},
        ])
        assert result == {"a": "1", "b": 42}

    def test_flattens_subcommand_options(self) -> None:
        """Subcommand (type 1) wrapper should be discarded, leaves kept."""
        result = _flatten_options([
            {
                "name": "create",
                "type": DISCORD_OPTION_TYPE_SUB_COMMAND,
                "options": [
                    {"name": "topic", "type": 3, "value": "Design Review"},
                    {"name": "team", "type": 3, "value": "Art"},
                ],
            },
        ])
        assert result == {"topic": "Design Review", "team": "Art"}
        # Subcommand wrapper name "create" is NOT in the result.
        assert "create" not in result

    def test_flattens_subcommand_group_options(self) -> None:
        """Subcommand group (type 2) with nested subcommand (type 1)."""
        result = _flatten_options([
            {
                "name": "admin",
                "type": DISCORD_OPTION_TYPE_SUB_COMMAND_GROUP,
                "options": [
                    {
                        "name": "config",
                        "type": DISCORD_OPTION_TYPE_SUB_COMMAND,
                        "options": [
                            {"name": "key", "type": 3, "value": "max_rounds"},
                            {"name": "value", "type": 3, "value": "5"},
                        ],
                    },
                ],
            },
        ])
        assert result == {"key": "max_rounds", "value": "5"}
        assert "admin" not in result
        assert "config" not in result

    def test_ignores_non_dict_entries(self) -> None:
        """Malformed entries (non-dict) should be silently skipped."""
        result = _flatten_options([
            "not a dict",
            {"name": "valid", "type": 3, "value": "ok"},
            None,
            42,
        ])
        assert result == {"valid": "ok"}

    def test_skips_option_without_name(self) -> None:
        """Option dicts without a 'name' key should be skipped."""
        result = _flatten_options([
            {"type": 3, "value": "no-name"},
            {"name": "has_name", "type": 3, "value": "present"},
        ])
        assert result == {"has_name": "present"}

    def test_skips_option_with_empty_name(self) -> None:
        """Option with empty string name should be skipped."""
        result = _flatten_options([
            {"name": "", "type": 3, "value": "empty-name"},
            {"name": "real", "type": 3, "value": "ok"},
        ])
        assert result == {"real": "ok"}

    def test_deeply_nested_stops_at_depth_limit(self) -> None:
        """Nesting beyond depth 4 returns empty dict (safety guard)."""
        deep = {
            "name": "level1",
            "type": 2,
            "options": [{
                "name": "level2",
                "type": 2,
                "options": [{
                    "name": "level3",
                    "type": 2,
                    "options": [{
                        "name": "level4",
                        "type": 2,
                        "options": [{
                            "name": "level5",
                            "type": 2,
                            "options": [{
                                "name": "leaf",
                                "type": 3,
                                "value": "too-deep",
                            }],
                        }],
                    }],
                }],
            }],
        }
        result = _flatten_options([deep])
        # Should return empty due to depth guard
        assert result == {}

    def test_parse_interaction_command_flattens_subcommand(self) -> None:
        """End-to-end: parse_interaction_command handles subcommand options."""
        payload = _make_payload(
            command_name="meeting",
            options=[
                {
                    "name": "create",
                    "type": 1,
                    "options": [
                        {"name": "agenda", "type": 3, "value": "Review"},
                    ],
                },
            ],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.options == {"agenda": "Review"}


# ═══════════════════════════════════════════════════════════════════════════
# 5. Payload envelope validation
# ═══════════════════════════════════════════════════════════════════════════


class TestEnvelopeValidation:
    """Tests for _validate_envelope (id, token, type checks)."""

    def test_valid_envelope_passes(self) -> None:
        payload = _make_payload()
        result = _validate_envelope(payload)
        assert result is None  # None means valid

    def test_missing_id(self) -> None:
        payload = {"token": "tok", "type": 2}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_ID"

    def test_empty_id(self) -> None:
        payload = {"id": "", "token": "tok", "type": 2}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_ID"

    def test_non_string_id(self) -> None:
        payload = {"id": 12345, "token": "tok", "type": 2}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_ID"

    def test_missing_token(self) -> None:
        payload = {"id": "abc", "type": 2}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_TOKEN"

    def test_empty_token(self) -> None:
        payload = {"id": "abc", "token": "", "type": 2}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_TOKEN"

    def test_non_string_token(self) -> None:
        payload = {"id": "abc", "token": 999, "type": 2}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_TOKEN"

    def test_missing_type(self) -> None:
        payload = {"id": "abc", "token": "tok"}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_TYPE"

    def test_type_as_string(self) -> None:
        payload = {"id": "abc", "token": "tok", "type": "2"}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "MISSING_TYPE"

    def test_unknown_type_value(self) -> None:
        payload = {"id": "abc", "token": "tok", "type": 99}
        result = _validate_envelope(payload)
        assert result is not None
        assert result.error_code == "UNKNOWN_TYPE"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Error conditions — JSON parsing and input formats
# ═══════════════════════════════════════════════════════════════════════════


class TestInputFormatsAndErrors:
    """Tests for various input formats and error handling."""

    def test_invalid_json_string(self) -> None:
        result = parse_interaction_command("not valid json {{{")
        assert_failure(result, error_code="INVALID_JSON")

    def test_non_dict_json(self) -> None:
        """A JSON array is not a valid interaction payload."""
        result = parse_interaction_command("[1, 2, 3]")
        assert_failure(result, error_code="NOT_A_DICT")

    def test_non_dict_input(self) -> None:
        """Direct non-dict input should fail."""
        result = parse_interaction_command([1, 2, 3])  # type: ignore[arg-type]
        assert_failure(result, error_code="NOT_A_DICT")

    def test_invalid_utf8_bytes(self) -> None:
        """Bytes that are not valid UTF-8 should fail cleanly."""
        result = parse_interaction_command(b"\x80\x81\x82")
        assert_failure(result, error_code="INVALID_UTF8")

    def test_valid_utf8_but_not_json(self) -> None:
        """Valid UTF-8 that is not JSON should fail."""
        result = parse_interaction_command(b"hello world")
        assert_failure(result, error_code="INVALID_JSON")

    def test_empty_dict(self) -> None:
        """An empty dict fails envelope validation (missing id)."""
        result = parse_interaction_command({})
        assert_failure(result, error_code="MISSING_ID")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Missing data object
# ═══════════════════════════════════════════════════════════════════════════


class TestMissingDataObject:
    """Tests for APPLICATION_COMMAND interactions with missing data."""

    def test_completely_missing_data_key(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "user": {"id": "u1"},
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_DATA")

    def test_data_is_not_a_dict(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "data": "not-a-dict",
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_DATA")

    def test_data_is_list(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "data": [1, 2, 3],
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_DATA")

    def test_data_is_none(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "data": None,
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_DATA")

    def test_data_missing_name(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "data": {"id": "d1", "type": 1},  # no 'name'
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_COMMAND_NAME")

    def test_data_empty_name(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "data": {"id": "d1", "name": "", "type": 1},
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_COMMAND_NAME")

    def test_data_name_is_not_string(self) -> None:
        payload = {
            "id": "abc",
            "token": "tok",
            "type": DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
            "data": {"id": "d1", "name": 123, "type": 1},
        }
        result = parse_interaction_command(payload)
        assert_failure(result, error_code="MISSING_COMMAND_NAME")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case tests for the parser."""

    def test_parsed_command_options_dataclass_fields(self) -> None:
        """Verify ParsedCommandOptions has expected fields."""
        pco = ParsedCommandOptions(
            command_name="test",
            options={"a": "b"},
        )
        assert pco.command_name == "test"
        assert pco.options == {"a": "b"}
        assert pco.raw_payload == {}

    def test_interaction_parse_result_success_shape(self) -> None:
        """Verify InteractionParseResult with success=True."""
        pco = ParsedCommandOptions(command_name="x", options={})
        r = InteractionParseResult(success=True, parsed=pco)
        assert r.success is True
        assert r.parsed is pco
        assert r.error == ""
        assert r.error_code == ""

    def test_interaction_parse_result_failure_shape(self) -> None:
        """Verify InteractionParseResult with success=False."""
        r = InteractionParseResult(
            success=False,
            error="Something went wrong",
            error_code="TEST_ERROR",
        )
        assert r.success is False
        assert r.parsed is None
        assert r.error == "Something went wrong"
        assert r.error_code == "TEST_ERROR"

    def test_parse_interaction_command_from_bytes_accepts_expected_name(self) -> None:
        """Convenience wrapper forwards expected_command_name."""
        payload = _make_payload_bytes(command_name="meeting")
        result = parse_interaction_command_from_bytes(
            payload,
            expected_command_name="meeting",
        )
        assert_success(result)

    def test_parse_interaction_command_from_bytes_rejects_mismatch(self) -> None:
        """Convenience wrapper validates expected_command_name."""
        payload = _make_payload_bytes(command_name="cancel")
        result = parse_interaction_command_from_bytes(
            payload,
            expected_command_name="meeting",
        )
        assert_failure(result, error_code="COMMAND_NAME_MISMATCH")

    def test_number_option_value_preserves_int_type(self) -> None:
        """Numeric option values should be Python int, not string."""
        payload = _make_payload(
            options=[{"name": "count", "type": 4, "value": 7}],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert isinstance(parsed.options["count"], int)
        assert parsed.options["count"] == 7

    def test_number_option_value_preserves_float_type(self) -> None:
        """Float option values should be Python float."""
        payload = _make_payload(
            options=[{"name": "ratio", "type": 10, "value": 0.85}],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert isinstance(parsed.options["ratio"], float)
        assert parsed.options["ratio"] == 0.85

    def test_boolean_option_value_false(self) -> None:
        """False boolean values should be preserved, not coerced."""
        payload = _make_payload(
            options=[{"name": "quiet", "type": 5, "value": False}],
        )
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.options["quiet"] is False

    def test_options_are_non_list_input(self) -> None:
        """If options is not a list, treat as empty."""
        payload = _make_payload()
        payload["data"]["options"] = "not-a-list"  # type: ignore[typeddict-item]
        result = parse_interaction_command(payload)
        parsed = assert_success(result)
        assert parsed.options == {}

    def test_constants_are_correct(self) -> None:
        """Verify the module-level constants."""
        assert DISCORD_INTERACTION_TYPE_PING == 1
        assert DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND == 2
        assert DISCORD_OPTION_TYPE_SUB_COMMAND == 1
        assert DISCORD_OPTION_TYPE_SUB_COMMAND_GROUP == 2
