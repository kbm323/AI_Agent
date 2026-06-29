"""Tests for Slash Command Interaction Orchestrator (Sub-AC 1b-iii).

Comprehensive tests for ``orchestrate_slash_command()`` with full mock
interaction payloads and a mocked meeting initiation function, as required
by the acceptance criterion.

Test categories
---------------
1.  Happy path — valid full meeting payload → MeetingContext returned
2.  Command name validation — mismatched, missing, custom expected name
3.  Parameter validation — empty/whitespace topic, invalid urgency,
    invalid team IDs, multiple simultaneous errors
4.  Team selection extraction — comma-separated string, list, empty
5.  Discord metadata extraction — guild member, DM user, missing fields
6.  Error propagation — parse failure → error response
7.  Meeting creation failure — orchestrator raises → error response
8.  Edge cases — Korean text, None option values, dict/str/bytes input
9.  Mock meeting function — verifies all fields of MeetingCommandRequest
10. Response format validation — type, data.content structure
11. OrchestratorResult invariants — success/error mutual exclusion
12. Default urgency — p2 when not provided
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import pytest

from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    MeetingManifest,
)
from src.slash_command_orchestrator import (
    MeetingCreatorFn,
    OrchestratorResult,
    orchestrate_slash_command,
)


# ── Mock meeting context builder ──────────────────────────────────────────


def _make_mock_manifest(meeting_id: str) -> MeetingManifest:
    """Build a minimal MeetingManifest for testing."""
    return MeetingManifest(
        meeting_id=meeting_id,
        state="created",
        priority="p2",
        agenda="Test agenda",
        created_at="2026-06-11T00:00:00Z",
        updated_at="2026-06-11T00:00:00Z",
    )


def _make_mock_context(
    meeting_id: str = "meeting_20260611_test0001",
) -> MeetingContext:
    """Build a minimal MeetingContext for testing."""
    return MeetingContext(
        meeting_id=meeting_id,
        manifest=_make_mock_manifest(meeting_id),
        meeting_dir=f"/tmp/test-meetings/{meeting_id}",
        manifest_path=f"/tmp/test-meetings/{meeting_id}/manifest.json",
        rounds_dir=f"/tmp/test-meetings/{meeting_id}/rounds",
        raw_outputs_dir=f"/tmp/test-meetings/{meeting_id}/raw_outputs",
        decisions_dir=f"/tmp/test-meetings/{meeting_id}/decisions",
        knowledge_dir=f"/tmp/test-meetings/{meeting_id}/knowledge",
        config=MeetingConfig(),
    )


# ── Mock meeting creator (spy) ────────────────────────────────────────────


@dataclass
class MockMeetingCreator:
    """A mock meeting creation function that records calls and returns a
    pre-configured MeetingContext.

    As a spy, it captures the last ``MeetingCommandRequest`` it received,
    enabling tests to verify the orchestrator is building requests correctly.
    """

    return_context: MeetingContext
    calls: list[MeetingCommandRequest] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    def __call__(
        self,
        request: MeetingCommandRequest,
        *,
        meetings_root: Optional[str] = None,
        config: Optional[MeetingConfig] = None,
    ) -> MeetingContext:
        self.calls.append(request)
        return self.return_context


def _make_raising_creator(exc: Exception) -> MeetingCreatorFn:
    """Return a meeting creator that always raises the given exception."""

    class RaisingCreator:
        def __call__(
            self,
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            raise exc

    return RaisingCreator()


# ── Payload builders ──────────────────────────────────────────────────────


def _make_meeting_payload(
    *,
    command_name: str = "meeting",
    agenda: Optional[str] = "Design Review",
    team_selection: Any = None,
    urgency: Optional[str] = None,
    interaction_id: str = "interaction-001",
    token: str = "tok-abc123",
    guild_id: str = "guild-789",
    channel_id: str = "channel-456",
    user_id: str = "user-111",
    user_name: str = "testuser",
    include_member: bool = True,
) -> dict[str, Any]:
    """Build a complete, valid Discord APPLICATION_COMMAND interaction payload.

    All fields are pre-populated with sensible defaults so individual
    tests only need to override what they are specifically testing.

    Args:
        command_name: The slash command name (e.g. "meeting").
        agenda: Value for the 'agenda' option. None omits the option.
        team_selection: Value for the 'team_selection' option.
        urgency: Value for the 'urgency' option.
        include_member: When True, wrap user_id in member.user (guild).
                       When False, use top-level user (DM).
    """
    payload: dict[str, Any] = {
        "id": interaction_id,
        "application_id": "app-999",
        "type": 2,  # APPLICATION_COMMAND
        "token": token,
        "version": 1,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "data": {
            "id": "cmd-data-001",
            "name": command_name,
            "type": 1,
        },
    }

    if include_member:
        payload["member"] = {
            "user": {"id": user_id, "username": user_name},
        }
    else:
        payload["user"] = {"id": user_id, "username": user_name}

    # Build options list
    options: list[dict[str, Any]] = []
    if agenda is not None:
        options.append({"name": "agenda", "type": 3, "value": agenda})
    if team_selection is not None:
        options.append({"name": "team_selection", "type": 3, "value": team_selection})
    if urgency is not None:
        options.append({"name": "urgency", "type": 3, "value": urgency})

    if options:
        payload["data"]["options"] = options

    return payload


def _make_meeting_payload_json(**kwargs: Any) -> str:
    """Build a payload and serialize it to a JSON string."""
    return json.dumps(_make_meeting_payload(**kwargs), ensure_ascii=False)


def _make_meeting_payload_bytes(**kwargs: Any) -> bytes:
    """Build a payload and serialize it to UTF-8 bytes."""
    return json.dumps(_make_meeting_payload(**kwargs),
                      ensure_ascii=False).encode("utf-8")


# ── Default mock context ──────────────────────────────────────────────────

_DEFAULT_CONTEXT = _make_mock_context()


# ── Helper assertions ─────────────────────────────────────────────────────


def assert_success(result: OrchestratorResult) -> MeetingContext:
    """Assert the result is a success and return the MeetingContext."""
    assert result.success is True, (
        f"Expected success, got error [{result.error_code}]: {result.error}"
    )
    assert result.meeting_context is not None
    return result.meeting_context


def assert_failure(
    result: OrchestratorResult,
    *,
    error_code: str,
    error_substring: Optional[str] = None,
) -> None:
    """Assert the result is a failure with the expected error code."""
    assert result.success is False
    assert result.error_code == error_code, (
        f"Expected error_code '{error_code}', got "
        f"'{result.error_code}': {result.error}"
    )
    if error_substring is not None:
        assert error_substring.lower() in result.error.lower(), (
            f"Expected error to contain '{error_substring}', "
            f"got: {result.error}"
        )


def assert_response_has_type(result: OrchestratorResult, response_type: int) -> None:
    """Assert the interaction_response has the expected Discord response type."""
    assert result.interaction_response["type"] == response_type, (
        f"Expected response type {response_type}, "
        f"got {result.interaction_response['type']}"
    )


def assert_response_content_contains(
    result: OrchestratorResult,
    substring: str,
) -> None:
    """Assert the response data.content contains the given substring."""
    content = result.interaction_response.get("data", {}).get("content", "")
    assert substring.lower() in content.lower(), (
        f"Expected response content to contain '{substring}', "
        f"got: {content[:200]}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path — full valid meeting payload
# ═══════════════════════════════════════════════════════════════════════════


class TestHappyPath:
    """Tests for successful orchestration with valid payloads."""

    def test_full_valid_payload_creates_meeting(self) -> None:
        """A complete valid payload should create a meeting and return context."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Q2 Roadmap Planning",
            team_selection="art-design,tech-engineering",
            urgency="p1",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        ctx = assert_success(result)
        assert ctx.meeting_id == _DEFAULT_CONTEXT.meeting_id
        assert_response_has_type(result, 4)
        assert_response_content_contains(result, "Meeting created")
        assert_response_content_contains(result, "Q2 Roadmap Planning")

    def test_minimal_payload_topic_only(self) -> None:
        """A payload with only the topic (agenda) should succeed with defaults."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Simple check-in")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        ctx = assert_success(result)
        assert ctx is not None
        assert len(mock.calls) == 1
        request = mock.calls[0]
        assert request.agenda == "Simple check-in"
        assert request.priority == "p2"  # default
        assert request.teams == ()  # no team filter

    def test_korean_topic_preserved(self) -> None:
        """Korean text in the topic should be preserved exactly."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="뮤직비디오 오프닝 아이디어 회의",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert len(mock.calls) == 1
        assert mock.calls[0].agenda == "뮤직비디오 오프닝 아이디어 회의"

    def test_response_includes_meeting_id(self) -> None:
        """The success response should include the meeting ID."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert_response_content_contains(
            result, _DEFAULT_CONTEXT.meeting_id
        )

    def test_response_includes_topic(self) -> None:
        """The success response should include the meeting topic."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="프로젝트 검토")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert_response_content_contains(result, "프로젝트 검토")

    def test_response_includes_priority(self) -> None:
        """The success response should include the priority level."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Urgent fix", urgency="p0")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert_response_content_contains(result, "P0")

    def test_response_includes_team_selection(self) -> None:
        """The success response should list the selected teams."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Team sync",
            team_selection="art-design,marketing",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert_response_content_contains(result, "art-design")
        assert_response_content_contains(result, "marketing")

    def test_no_team_selection_shows_all_teams(self) -> None:
        """When no team is selected, the response should say 'All teams'."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="General meeting")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert_response_content_contains(result, "All teams")

    def test_dict_payload_accepted(self) -> None:
        """Dict input should work directly."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Dict test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)

    def test_json_string_payload_accepted(self) -> None:
        """JSON string input should be parsed transparently."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload_json(agenda="JSON string test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)

    def test_bytes_payload_accepted(self) -> None:
        """UTF-8 bytes input should be parsed transparently."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload_bytes(agenda="Bytes test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Command name validation
# ═══════════════════════════════════════════════════════════════════════════


class TestCommandNameValidation:
    """Tests for slash command name matching."""

    def test_meeting_command_name_accepted(self) -> None:
        """Default expected command name 'meeting' should pass."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            command_name="meeting",
            agenda="Test",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)

    def test_mismatched_command_name_rejected(self) -> None:
        """A non-matching command name should fail with parse error."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            command_name="status",
            agenda="check",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="COMMAND_NAME_MISMATCH")
        assert len(mock.calls) == 0  # Creator should not be called

    def test_custom_expected_command_name(self) -> None:
        """Custom expected_command_name should be respected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            command_name="start-meeting",
            agenda="Custom command test",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
            expected_command_name="start-meeting",
        )

        assert_success(result)

    def test_custom_expected_command_mismatch(self) -> None:
        """Custom expected name mismatch should fail."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            command_name="meeting",
            agenda="Test",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
            expected_command_name="start-meeting",
        )

        assert_failure(result, error_code="COMMAND_NAME_MISMATCH")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Parameter validation
# ═══════════════════════════════════════════════════════════════════════════


class TestParameterValidation:
    """Tests for meeting parameter validation within the orchestrator."""

    def test_empty_agenda_rejected(self) -> None:
        """An empty agenda string should be rejected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        assert_response_content_contains(result, "EMPTY_TOPIC")
        assert len(mock.calls) == 0

    def test_whitespace_only_agenda_rejected(self) -> None:
        """A whitespace-only agenda should be rejected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="   ")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        assert_response_content_contains(result, "EMPTY_TOPIC")

    def test_invalid_urgency_rejected(self) -> None:
        """An invalid urgency tier should be rejected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test", urgency="p5")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        assert_response_content_contains(result, "INVALID_URGENCY")

    def test_invalid_team_id_rejected(self) -> None:
        """An unrecognised team ID should be rejected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            team_selection="invalid-team,art-design",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        assert_response_content_contains(result, "INVALID_TEAM_ID")

    def test_multiple_validation_errors_collected(self) -> None:
        """All validation errors should be collected before returning."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="",
            urgency="invalid",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        # Both errors should appear
        assert_response_content_contains(result, "EMPTY_TOPIC")
        assert_response_content_contains(result, "INVALID_URGENCY")

    def test_valid_urgency_tiers_accepted(self) -> None:
        """All p0-p3 urgency tiers should be accepted."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        for urgency in ("p0", "p1", "p2", "p3"):
            payload = _make_meeting_payload(agenda="Test", urgency=urgency)
            result = orchestrate_slash_command(
                payload,
                create_meeting_fn=mock,
            )
            assert result.success, (
                f"Urgency '{urgency}' should be valid, got: {result.error}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Team selection extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestTeamSelectionExtraction:
    """Tests for team_selection option extraction and processing."""

    def test_comma_separated_string_split(self) -> None:
        """A comma-separated string should be split into a list."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            team_selection="art-design,tech-engineering,marketing",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        request = mock.calls[0]
        # The validator sorts and deduplicates team_selection
        assert "art-design" in request.teams
        assert "tech-engineering" in request.teams
        assert "marketing" in request.teams

    def test_comma_separated_with_whitespace(self) -> None:
        """Whitespace around commas should be stripped."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            team_selection=" art-design ,  tech-engineering ",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        request = mock.calls[0]
        assert "art-design" in request.teams
        assert "tech-engineering" in request.teams

    def test_missing_team_selection_is_empty(self) -> None:
        """When team_selection is not provided, teams should be empty."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        # team_selection not included at all

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].teams == ()

    def test_team_selection_as_list_in_options(self) -> None:
        """When Discord delivers team_selection as a list, it should work.

        (This can happen with certain slash command option configurations
        or multi-select setups.)
        """
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        # Build payload manually to inject a list value
        payload = _make_meeting_payload(agenda="Test")
        payload["data"]["options"] = [
            {"name": "agenda", "type": 3, "value": "Test"},
            {"name": "team_selection", "type": 3,
             "value": ["art-design", "content-production"]},
        ]

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        request = mock.calls[0]
        assert "art-design" in request.teams
        assert "content-production" in request.teams


# ═══════════════════════════════════════════════════════════════════════════
# 5. Discord metadata extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscordMetadataExtraction:
    """Tests for extracting user_id, channel_id, guild_id from payloads."""

    def test_guild_member_user_id_extracted(self) -> None:
        """User ID from member.user (guild context) should be extracted."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            user_id="discord-user-42",
            include_member=True,
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].user_id == "discord-user-42"

    def test_dm_user_id_extracted(self) -> None:
        """User ID from top-level user (DM context) should be extracted."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            user_id="dm-user-99",
            include_member=False,
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].user_id == "dm-user-99"

    def test_channel_id_extracted(self) -> None:
        """Channel ID should be extracted from the payload."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            channel_id="channel-789",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].channel_id == "channel-789"

    def test_guild_id_extracted(self) -> None:
        """Guild ID should be extracted from the payload."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            guild_id="guild-999",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].guild_id == "guild-999"

    def test_missing_user_id_falls_back_to_unknown(self) -> None:
        """When user_id is completely absent, fall back to 'unknown'."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        # Remove both member and user
        payload.pop("member", None)
        payload.pop("user", None)

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        # Should still succeed — the validator validates the params
        assert_success(result)
        assert mock.calls[0].user_id == "unknown"

    def test_missing_channel_id_falls_back_to_unknown(self) -> None:
        """When channel_id is absent, fall back to 'unknown'."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        payload.pop("channel_id", None)

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].channel_id == "unknown"

    def test_missing_guild_id_is_empty_string(self) -> None:
        """When guild_id is absent (DM), it should be empty string."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        payload.pop("guild_id", None)

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].guild_id == ""


# ═══════════════════════════════════════════════════════════════════════════
# 6. Error propagation — parse failures
# ═══════════════════════════════════════════════════════════════════════════


class TestParseFailurePropagation:
    """Tests that parse errors from the parser are propagated correctly."""

    def test_invalid_json_rejected(self) -> None:
        """Invalid JSON string should produce an error response."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)

        result = orchestrate_slash_command(
            "not valid json {",
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="INVALID_JSON")
        assert_response_has_type(result, 4)
        assert_response_content_contains(result, "INVALID_JSON")
        assert len(mock.calls) == 0

    def test_missing_type_rejected(self) -> None:
        """Missing type field should produce an error response."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = {"id": "abc", "token": "tok"}

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="MISSING_TYPE")

    def test_ping_interaction_rejected(self) -> None:
        """A PING interaction should be gracefully rejected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = {
            "id": "ping-001",
            "token": "tok-ping",
            "type": 1,
            "application_id": "app-999",
            "version": 1,
        }

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="NOT_APPLICATION_COMMAND")

    def test_missing_data_object_rejected(self) -> None:
        """Missing data object should be rejected."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        del payload["data"]

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="MISSING_DATA")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Meeting creation failure
# ═══════════════════════════════════════════════════════════════════════════


class TestMeetingCreationFailure:
    """Tests for when the meeting creation callable raises."""

    def test_creator_raises_value_error(self) -> None:
        """When the creator raises ValueError, it should be caught."""
        creator = _make_raising_creator(ValueError("agenda must not be empty"))
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=creator,
        )

        assert_failure(result, error_code="MEETING_CREATION_FAILED")
        assert_response_content_contains(result, "ValueError")
        assert_response_has_type(result, 4)

    def test_creator_raises_os_error(self) -> None:
        """OSError from the creator (e.g. disk full) should be caught."""
        creator = _make_raising_creator(
            OSError("No space left on device"),
        )
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=creator,
        )

        assert_failure(result, error_code="MEETING_CREATION_FAILED")
        assert "OSError" in result.error

    def test_creator_raises_runtime_error(self) -> None:
        """Any exception from the creator should be caught."""
        creator = _make_raising_creator(
            RuntimeError("Something unexpected happened"),
        )
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=creator,
        )

        assert_failure(result, error_code="MEETING_CREATION_FAILED")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Mock meeting function — request field verification
# ═══════════════════════════════════════════════════════════════════════════


class TestMockMeetingFunctionRequestFields:
    """Tests that verify the MeetingCommandRequest built by the orchestrator.

    These tests use the spy (MockMeetingCreator) to inspect every field
    of the request passed to the meeting creation callable.
    """

    def test_request_agenda_set_correctly(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="  API 설계 논의  ")

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        # Topic should be stripped by the validator
        assert mock.calls[0].agenda == "API 설계 논의"

    def test_request_priority_mapped_from_urgency(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test", urgency="p0")

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert mock.calls[0].priority == "p0"

    def test_request_default_priority_is_p2(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        # urgency not provided

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert mock.calls[0].priority == "p2"

    def test_request_teams_from_team_selection(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            team_selection="validation,execution",
        )

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert "validation" in mock.calls[0].teams
        assert "execution" in mock.calls[0].teams

    def test_request_suggested_roles_empty(self) -> None:
        """Slash commands don't provide role-level constraints — should be empty."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert mock.calls[0].suggested_roles == ()

    def test_request_user_id_from_payload(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            user_id="user-specific-123",
        )

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert mock.calls[0].user_id == "user-specific-123"

    def test_request_channel_id_from_payload(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            channel_id="channel-specific-456",
        )

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert mock.calls[0].channel_id == "channel-specific-456"

    def test_request_guild_id_from_payload(self) -> None:
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            guild_id="guild-specific-789",
        )

        orchestrate_slash_command(payload, create_meeting_fn=mock)

        assert mock.calls[0].guild_id == "guild-specific-789"


# ═══════════════════════════════════════════════════════════════════════════
# 9. Response format validation
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseFormat:
    """Tests for the structure and format of the Discord interaction response."""

    def test_success_response_has_type_4(self) -> None:
        """Success should use type 4 (CHANNEL_MESSAGE_WITH_SOURCE)."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert result.interaction_response["type"] == 4

    def test_error_response_has_type_4(self) -> None:
        """Errors should still use type 4 so the user sees inline feedback."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        assert result.interaction_response["type"] == 4

    def test_success_response_has_data_content(self) -> None:
        """The success response should have data.content string."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        data = result.interaction_response.get("data")
        assert isinstance(data, dict)
        assert isinstance(data.get("content"), str)
        assert len(data["content"]) > 0

    def test_error_response_has_data_content(self) -> None:
        """The error response should have data.content string with error info."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        data = result.interaction_response.get("data")
        assert isinstance(data, dict)
        assert isinstance(data.get("content"), str)

    def test_success_response_is_json_serializable(self) -> None:
        """The interaction_response dict should be JSON-serializable."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        serialized = json.dumps(result.interaction_response, ensure_ascii=False)
        assert len(serialized) > 0

    def test_error_response_is_json_serializable(self) -> None:
        """Error responses should also be JSON-serializable."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_failure(result, error_code="VALIDATION_FAILED")
        serialized = json.dumps(result.interaction_response, ensure_ascii=False)
        assert len(serialized) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 10. OrchestratorResult invariants
# ═══════════════════════════════════════════════════════════════════════════


class TestOrchestratorResultInvariants:
    """Tests for the invariants of the OrchestratorResult dataclass."""

    def test_success_result_has_no_error_fields(self) -> None:
        """On success, error and error_code should be empty strings."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert result.success
        assert result.error == ""
        assert result.error_code == ""

    def test_failure_result_has_no_meeting_context(self) -> None:
        """On failure, meeting_context should be None."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert not result.success
        assert result.meeting_context is None

    def test_interaction_response_always_populated(self) -> None:
        """Both success and failure should always have interaction_response."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)

        # Success case
        result = orchestrate_slash_command(
            _make_meeting_payload(agenda="Test"),
            create_meeting_fn=mock,
        )
        assert result.interaction_response is not None
        assert len(result.interaction_response) > 0

        # Failure case
        result = orchestrate_slash_command(
            _make_meeting_payload(agenda=""),
            create_meeting_fn=mock,
        )
        assert result.interaction_response is not None
        assert len(result.interaction_response) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 11. Default urgency
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultUrgency:
    """Tests for urgency/priority default behaviour."""

    def test_default_urgency_is_p2_when_not_provided(self) -> None:
        """When the urgency option is absent, default to p2."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test")
        # urgency not included in options

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].priority == "p2"

    def test_default_urgency_is_p2_when_empty_string(self) -> None:
        """When urgency is an empty string, default to p2."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test", urgency="")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].priority == "p2"

    def test_urgency_case_insensitive(self) -> None:
        """Urgency should be case-insensitive (the validator lowercases)."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test", urgency="P1")

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].priority == "p1"


# ═══════════════════════════════════════════════════════════════════════════
# 12. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_very_long_topic_accepted(self) -> None:
        """A topic near the max length should be accepted."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        long_topic = "A" * 3900  # well under 4000
        payload = _make_meeting_payload(agenda=long_topic)

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)

    def test_duplicate_teams_deduplicated(self) -> None:
        """Duplicate team entries should be deduplicated by the validator."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="Test",
            team_selection="art-design,art-design,art-design",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        # Should only have one "art-design"
        assert mock.calls[0].teams == ("art-design",)

    def test_topic_fallback_option_name(self) -> None:
        """When 'agenda' is not present, 'topic' option should be used."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda=None)  # no 'agenda' option
        payload["data"]["options"] = [
            {"name": "topic", "type": 3, "value": "Fallback topic"},
        ]

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].agenda == "Fallback topic"

    def test_urgency_fallback_option_name(self) -> None:
        """When 'urgency' is not present, 'priority' option should be used."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(agenda="Test", urgency=None)
        payload["data"]["options"] = [
            {"name": "agenda", "type": 3, "value": "Test"},
            {"name": "priority", "type": 3, "value": "p0"},
        ]

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        assert_success(result)
        assert mock.calls[0].priority == "p0"

    def test_leaderboard_example_company_meeting(self) -> None:
        """A realistic example: entertainment company creative review."""
        mock = MockMeetingCreator(return_context=_DEFAULT_CONTEXT)
        payload = _make_meeting_payload(
            agenda="신규 버추얼 아이돌 '스텔라' 데뷔 컨셉 회의",
            team_selection="content-production,art-design,marketing",
            urgency="p1",
            user_id="discord-ceo-001",
            channel_id="channel-boardroom",
        )

        result = orchestrate_slash_command(
            payload,
            create_meeting_fn=mock,
        )

        ctx = assert_success(result)
        assert ctx is not None
        request = mock.calls[0]
        assert "스텔라" in request.agenda
        assert request.priority == "p1"
        assert "content-production" in request.teams
        assert "art-design" in request.teams
        assert "marketing" in request.teams
        assert request.user_id == "discord-ceo-001"
        assert_response_content_contains(result, "스텔라")
        assert_response_content_contains(result, "P1")


# ── Existing doctest helpers (used by module docstring examples) ──────────
# These must be defined at module level so doctest can find them.


def _make_meeting_payload_for_doctest(**kwargs: Any) -> dict[str, Any]:
    """Shorthand alias used in doctest examples.  We reuse the real builder."""
    return _make_meeting_payload(**kwargs)


def _mock_create_meeting(
    request: MeetingCommandRequest,
    *,
    meetings_root: Optional[str] = None,
    config: Optional[MeetingConfig] = None,
) -> MeetingContext:
    """Mock meeting creator for doctest examples."""
    return _DEFAULT_CONTEXT
