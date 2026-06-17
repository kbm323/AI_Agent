"""Tests for trigger input parsing and normalization (Sub-AC 3.1).

Verifies that ``parse_meeting_request()``:
- Parses raw payloads from all four trigger sources
- Validates required fields (agenda, user_id, channel_id)
- Enforces field constraints (length limits, Discord snowflake format)
- Normalises priority, strips whitespace, handles aliases
- Rejects invalid meeting types, priorities, and snowflakes
- Supports field overrides for side-channel data
- Produces correctly structured ParseResult objects
- Bridges to downstream pipeline via to_command_request()
  and meeting_request_to_parsed_trigger_input()

Test categories:
1. MeetingRequest dataclass — construction, validation, immutability
2. Discord slash command parsing
3. Discord @bot mention parsing
4. Webhook payload parsing
5. Direct API parsing
6. Priority validation and normalisation
7. Field override mechanism
8. Edge cases — empty payloads, whitespace-only, long strings
9. TriggerSource enum
10. Bridge to downstream pipeline
"""

from __future__ import annotations

import pytest

from src.trigger_input_parser import (
    MeetingRequest,
    ParseResult,
    TriggerSource,
    _RawParsed,
    _apply_overrides,
    _coerce_float,
    _coerce_tuple,
    _extract_string_list,
    _first_non_empty,
    _normalise_and_validate,
    _parse_direct_api,
    _parse_discord_mention,
    _parse_discord_slash,
    _parse_webhook,
    _safe_get,
    meeting_request_to_parsed_trigger_input,
    parse_meeting_request,
)


# ═════════════════════════════════════════════════════════════════════════
# MeetingRequest dataclass
# ═════════════════════════════════════════════════════════════════════════


class TestMeetingRequestConstruction:
    """Happy-path construction and basic field access."""

    def test_minimal_construction(self):
        req = MeetingRequest(
            source=TriggerSource.DIRECT_API,
            agenda="신규 캐릭터 디자인 검토",
            user_id="discord_user_12345",
            channel_id="discord_channel_67890",
        )
        assert req.agenda == "신규 캐릭터 디자인 검토"
        assert req.user_id == "discord_user_12345"
        assert req.channel_id == "discord_channel_67890"
        assert req.priority == "p2"  # default
        assert req.meeting_type == ""
        assert req.source == TriggerSource.DIRECT_API

    def test_full_construction(self):
        req = MeetingRequest(
            source=TriggerSource.DISCORD_SLASH,
            agenda="뮤직비디오 오프닝 아이디어",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            meeting_type="creative_production",
            priority="p1",
            thread_id="111111111111111111",
            guild_id="222222222222222222",
            message_id="333333333333333333",
            participants=("art-director", "concept-artist"),
            teams=("art-director",),
            suggested_roles=("concept-artist", "music-director"),
            raw_tags=("music_video", "creative"),
            confidence=0.95,
            reasoning="Parsed from slash command",
        )
        assert req.priority == "p1"
        assert req.meeting_type == "creative_production"
        assert len(req.participants) == 2
        assert len(req.teams) == 1
        assert len(req.suggested_roles) == 2
        assert len(req.raw_tags) == 2

    def test_default_fields(self):
        req = MeetingRequest(
            source=TriggerSource.DIRECT_API,
            agenda="test",
            user_id="u1",
            channel_id="c1",
        )
        assert req.priority == "p2"
        assert req.meeting_type == ""
        assert req.thread_id == ""
        assert req.guild_id == ""
        assert req.message_id == ""
        assert req.participants == ()
        assert req.teams == ()
        assert req.suggested_roles == ()
        assert req.raw_tags == ()
        assert req.confidence == 1.0
        assert req.reasoning == ""


class TestMeetingRequestValidation:
    """Field-level validation on MeetingRequest construction."""

    def test_empty_agenda_raises(self):
        with pytest.raises(ValueError, match="agenda must not be empty"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="",
                user_id="u1",
                channel_id="c1",
            )

    def test_whitespace_only_agenda_raises(self):
        with pytest.raises(ValueError, match="agenda must not be empty"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="   ",
                user_id="u1",
                channel_id="c1",
            )

    def test_empty_user_id_raises(self):
        with pytest.raises(ValueError, match="user_id must not be empty"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="",
                channel_id="c1",
            )

    def test_empty_channel_id_raises(self):
        with pytest.raises(ValueError, match="channel_id must not be empty"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="u1",
                channel_id="",
            )

    def test_invalid_priority_raises(self):
        with pytest.raises(ValueError, match="invalid priority"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="u1",
                channel_id="c1",
                priority="p5",
            )

    def test_invalid_meeting_type_raises(self):
        with pytest.raises(ValueError, match="invalid meeting_type"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="u1",
                channel_id="c1",
                meeting_type="invalid_type",
            )

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValueError, match="confidence must be"):
            MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="u1",
                channel_id="c1",
                confidence=1.5,
            )

    def test_all_valid_priorities_accepted(self):
        for p in ("p0", "p1", "p2", "p3"):
            req = MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="u1",
                channel_id="c1",
                priority=p,
            )
            assert req.priority == p

    def test_all_valid_meeting_types_accepted(self):
        valid = (
            "creative_production",
            "technical_development",
            "marketing_strategy",
            "risk_assessment",
            "general_planning",
            "project_review",
        )
        for mt in valid:
            req = MeetingRequest(
                source=TriggerSource.DIRECT_API,
                agenda="test",
                user_id="u1",
                channel_id="c1",
                meeting_type=mt,
            )
            assert req.meeting_type == mt


class TestMeetingRequestSerialization:
    """to_dict() and to_command_request() methods."""

    def test_to_dict(self):
        req = MeetingRequest(
            source=TriggerSource.DIRECT_API,
            agenda="회의 아젠다",
            user_id="user_1",
            channel_id="ch_1",
            priority="p1",
            meeting_type="general_planning",
            participants=("role_a", "role_b"),
        )
        d = req.to_dict()
        assert d["source"] == "direct_api"
        assert d["agenda"] == "회의 아젠다"
        assert d["user_id"] == "user_1"
        assert d["priority"] == "p1"
        assert d["participants"] == ["role_a", "role_b"]

    def test_to_command_request(self):
        req = MeetingRequest(
            source=TriggerSource.DISCORD_SLASH,
            agenda="뮤비 기획 회의",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            priority="p1",
            thread_id="111111111111111111",
            guild_id="222222222222222222",
            teams=("art-director",),
            suggested_roles=("concept-artist",),
        )
        cmd = req.to_command_request()
        assert cmd.agenda == "뮤비 기획 회의"
        assert cmd.user_id == "123456789012345678"
        assert cmd.channel_id == "987654321098765432"
        assert cmd.priority == "p1"
        assert cmd.thread_id == "111111111111111111"
        assert cmd.guild_id == "222222222222222222"
        assert cmd.teams == ("art-director",)
        assert cmd.suggested_roles == ("concept-artist",)


class TestMeetingRequestImmutability:
    """Frozen dataclass cannot be mutated."""

    def test_cannot_set_attribute(self):
        req = MeetingRequest(
            source=TriggerSource.DIRECT_API,
            agenda="test",
            user_id="u1",
            channel_id="c1",
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            req.agenda = "new"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# parse_meeting_request() — source dispatch
# ═════════════════════════════════════════════════════════════════════════


class TestParseMeetingRequestDiscordSlash:
    """Discord slash command payload parsing."""

    def test_basic_slash(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [
                        {"name": "agenda", "value": "캐릭터 디자인 회의"},
                        {"name": "priority", "value": "p1"},
                    ],
                },
                "member": {"user": {"id": "123456789012345678"}},
                "channel_id": "987654321098765432",
                "guild_id": "222222222222222222",
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert result.success
        assert result.request.agenda == "캐릭터 디자인 회의"
        assert result.request.priority == "p1"
        assert result.request.user_id == "123456789012345678"
        assert result.request.channel_id == "987654321098765432"
        assert result.request.guild_id == "222222222222222222"
        assert result.request.source == TriggerSource.DISCORD_SLASH

    def test_slash_with_meeting_type(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [
                        {"name": "agenda", "value": "API 설계"},
                        {"name": "meeting_type", "value": "technical_development"},
                    ],
                },
                "member": {"user": {"id": "123456789012345678"}},
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert result.success
        assert result.request.meeting_type == "technical_development"

    def test_slash_without_options(self):
        result = parse_meeting_request(
            payload={
                "data": {"name": "meeting"},
                "member": {"user": {"id": "123456789012345678"}},
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success
        assert "agenda" in result.error

    def test_slash_empty_agenda(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [
                        {"name": "agenda", "value": ""},
                    ],
                },
                "member": {"user": {"id": "123456789012345678"}},
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success
        assert "agenda" in result.error

    def test_slash_invalid_user_snowflake(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [
                        {"name": "agenda", "value": "test"},
                    ],
                },
                "member": {"user": {"id": "not_a_snowflake"}},
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success
        assert "snowflake" in result.error.lower() or "user_id" in result.error.lower()

    def test_slash_invalid_channel_snowflake(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [
                        {"name": "agenda", "value": "test"},
                    ],
                },
                "member": {"user": {"id": "123456789012345678"}},
                "channel_id": "abc",
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success
        assert "snowflake" in result.error.lower() or "channel_id" in result.error.lower()


class TestParseMeetingRequestDiscordMention:
    """Discord @bot mention payload parsing."""

    def test_basic_mention(self):
        result = parse_meeting_request(
            payload={
                "content": "새로운 캐릭터 디자인 검토 부탁해요",
                "author_id": "123456789012345678",
                "author_name": "pd_kim",
                "channel_id": "987654321098765432",
                "guild_id": "222222222222222222",
                "message_id": "333333333333333333",
                "is_command": True,
            },
            source=TriggerSource.DISCORD_MENTION,
        )
        assert result.success
        assert result.request.agenda == "새로운 캐릭터 디자인 검토 부탁해요"
        assert result.request.user_id == "123456789012345678"
        assert result.request.channel_id == "987654321098765432"
        assert result.request.guild_id == "222222222222222222"
        assert result.request.message_id == "333333333333333333"
        assert result.request.source == TriggerSource.DISCORD_MENTION

    def test_mention_empty_content(self):
        result = parse_meeting_request(
            payload={
                "content": "",
                "author_id": "123456789012345678",
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_MENTION,
        )
        assert not result.success
        assert "agenda" in result.error

    def test_mention_missing_author_id(self):
        result = parse_meeting_request(
            payload={
                "content": "test meeting",
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_MENTION,
        )
        assert not result.success
        assert "user_id" in result.error

    def test_mention_invalid_snowflake(self):
        result = parse_meeting_request(
            payload={
                "content": "test",
                "author_id": "bad_id",
                "channel_id": "987654321098765432",
            },
            source=TriggerSource.DISCORD_MENTION,
        )
        assert not result.success


class TestParseMeetingRequestWebhook:
    """Webhook payload parsing with field aliases."""

    def test_basic_webhook(self):
        result = parse_meeting_request(
            payload={
                "agenda": "신규 프로젝트 킥오프",
                "user_id": "webhook_user_1",
                "channel_id": "webhook_ch_1",
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.agenda == "신규 프로젝트 킥오프"
        assert result.request.source == TriggerSource.WEBHOOK

    def test_webhook_with_aliases(self):
        """Field aliases: topic→agenda, user→user_id, etc."""
        result = parse_meeting_request(
            payload={
                "topic": "마케팅 전략 회의",
                "user": "marketing_lead",
                "channel_id": "ch_1",
                "tags": ["marketing", "q3"],
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.agenda == "마케팅 전략 회의"
        assert result.request.user_id == "marketing_lead"
        assert result.request.raw_tags == ("marketing", "q3")

    def test_webhook_with_author_alias(self):
        result = parse_meeting_request(
            payload={
                "text": "review meeting",
                "author": "author_1",
                "channel_id": "ch_1",
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.agenda == "review meeting"
        assert result.request.user_id == "author_1"

    def test_webhook_with_content_alias(self):
        result = parse_meeting_request(
            payload={
                "content": "content is agenda",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.agenda == "content is agenda"

    def test_webhook_agenda_takes_priority_over_aliases(self):
        """When both 'agenda' and 'topic' are present, 'agenda' wins."""
        result = parse_meeting_request(
            payload={
                "agenda": "primary agenda",
                "topic": "secondary topic",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.agenda == "primary agenda"

    def test_webhook_with_participants(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "participants": ["role_a", "role_b", "role_c"],
                "teams": ["art-director"],
                "suggested_roles": ["concept-artist"],
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.participants == ("role_a", "role_b", "role_c")
        assert result.request.teams == ("art-director",)
        assert result.request.suggested_roles == ("concept-artist",)

    def test_webhook_comma_separated_tags(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "tags": "tag1, tag2, tag3",
            },
            source=TriggerSource.WEBHOOK,
        )
        assert result.success
        assert result.request.raw_tags == ("tag1", "tag2", "tag3")

    def test_webhook_invalid_meeting_type(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "meeting_type": "bad_type",
            },
            source=TriggerSource.WEBHOOK,
        )
        assert not result.success
        assert "meeting_type" in result.error

    def test_webhook_invalid_priority(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "priority": "urgent",
            },
            source=TriggerSource.WEBHOOK,
        )
        # Invalid priority defaults to p2 — not rejected
        # because the raw parser coerces it silently
        assert result.success
        assert result.request.priority == "p2"


class TestParseMeetingRequestDirectApi:
    """Direct API payload parsing."""

    def test_minimal_direct_api(self):
        result = parse_meeting_request(
            payload={
                "agenda": "API payload test",
                "user_id": "api_user_1",
                "channel_id": "api_ch_1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success
        assert result.request.agenda == "API payload test"
        assert result.request.priority == "p2"  # default

    def test_full_direct_api(self):
        result = parse_meeting_request(
            payload={
                "agenda": "Full payload test",
                "user_id": "user_1",
                "channel_id": "ch_1",
                "meeting_type": "risk_assessment",
                "priority": "p0",
                "thread_id": "th_1",
                "guild_id": "g_1",
                "message_id": "msg_1",
                "participants": ["role_1", "role_2"],
                "teams": ["team_1"],
                "suggested_roles": ["s_role_1"],
                "tags": ["risk", "security"],
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success
        assert result.request.meeting_type == "risk_assessment"
        assert result.request.priority == "p0"
        assert result.request.participants == ("role_1", "role_2")
        assert result.request.raw_tags == ("risk", "security")

    def test_direct_api_empty_agenda(self):
        result = parse_meeting_request(
            payload={
                "agenda": "",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert not result.success
        assert "agenda" in result.error

    def test_direct_api_missing_user_id(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert not result.success
        assert "user_id" in result.error

    def test_direct_api_no_snowflake_required(self):
        """Direct API does not enforce Discord snowflake format."""
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "any_user_id_format",
                "channel_id": "any_channel",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success


# ═════════════════════════════════════════════════════════════════════════
# Field overrides
# ═════════════════════════════════════════════════════════════════════════


class TestFieldOverrides:
    """Overrides mechanism for side-channel data."""

    def test_override_agenda(self):
        result = parse_meeting_request(
            payload={
                "agenda": "original",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
            agenda="overridden agenda",
        )
        assert result.success
        assert result.request.agenda == "overridden agenda"

    def test_override_user_id_for_webhook(self):
        """Webhook without user_id can get it via override."""
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "channel_id": "c1",
            },
            source=TriggerSource.WEBHOOK,
            user_id="override_user",
        )
        assert result.success
        assert result.request.user_id == "override_user"

    def test_override_channel_id(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "original",
            },
            source=TriggerSource.DIRECT_API,
            channel_id="overridden_channel",
        )
        assert result.success
        assert result.request.channel_id == "overridden_channel"

    def test_override_priority(self):
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "priority": "p2",
            },
            source=TriggerSource.DIRECT_API,
            priority="p0",
        )
        assert result.success
        assert result.request.priority == "p0"

    def test_override_invalid_priority_rejected(self):
        """Overrides with invalid priority are still rejected."""
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
            priority="p5",
        )
        assert not result.success
        assert "priority" in result.error

    def test_override_unknown_key_ignored(self):
        """Unknown override keys are silently ignored."""
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
            unknown_key="should be ignored",
        )
        assert result.success  # no error

    def test_override_fixes_missing_user_id_for_webhook(self):
        """Override supplies missing required fields for webhook."""
        result = parse_meeting_request(
            payload={
                "agenda": "webhook meeting",
            },
            source=TriggerSource.WEBHOOK,
            user_id="override_u1",
            channel_id="override_c1",
        )
        assert result.success
        assert result.request.user_id == "override_u1"
        assert result.request.channel_id == "override_c1"


# ═════════════════════════════════════════════════════════════════════════
# Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_whitespace_only_agenda_rejected(self):
        result = parse_meeting_request(
            payload={
                "agenda": "   \n\t  ",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert not result.success
        assert "agenda" in result.error

    def test_agenda_stripped(self):
        result = parse_meeting_request(
            payload={
                "agenda": "  leading and trailing spaces  ",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success
        assert result.request.agenda == "leading and trailing spaces"

    def test_long_agenda_accepted(self):
        """Agendas up to 4000 chars pass."""
        long_agenda = "A" * 4000
        result = parse_meeting_request(
            payload={
                "agenda": long_agenda,
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success

    def test_too_long_agenda_rejected(self):
        """Agendas over 4000 chars rejected."""
        too_long = "A" * 4001
        result = parse_meeting_request(
            payload={
                "agenda": too_long,
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert not result.success
        assert "4000" in result.error

    def test_priority_case_normalised(self):
        """Priority is lowercased: P1 → p1."""
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "priority": "P1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success
        assert result.request.priority == "p1"

    def test_unknown_priority_silently_defaulted(self):
        """For direct API: invalid priority defaults to p2."""
        result = parse_meeting_request(
            payload={
                "agenda": "test",
                "user_id": "u1",
                "channel_id": "c1",
                "priority": "urgent",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success
        assert result.request.priority == "p2"

    def test_parse_result_failure_structure(self):
        result = parse_meeting_request(
            payload={"agenda": ""},
            source=TriggerSource.DIRECT_API,
        )
        assert not result.success
        assert result.request is None
        assert result.error is not None
        assert "agenda" in result.error

    def test_parse_result_success_structure(self):
        result = parse_meeting_request(
            payload={
                "agenda": "valid",
                "user_id": "u1",
                "channel_id": "c1",
            },
            source=TriggerSource.DIRECT_API,
        )
        assert result.success
        assert result.request is not None
        assert result.error is None


class TestDiscordSnowflakeValidation:
    """Snowflake ID validation for Discord sources."""

    _VALID_SNOWFLAKE = "123456789012345678"  # 18 digits
    _VALID_SNOWFLAKE_17 = "12345678901234567"  # 17 digits
    _VALID_SNOWFLAKE_20 = "12345678901234567890"  # 20 digits
    _INVALID_SHORT = "123"  # too short
    _INVALID_LONG = "123456789012345678901"  # 21 digits
    _INVALID_ALPHA = "abc123456789012345"

    def test_valid_snowflake_accepted(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [{"name": "agenda", "value": "test"}],
                },
                "member": {"user": {"id": self._VALID_SNOWFLAKE}},
                "channel_id": self._VALID_SNOWFLAKE_17,
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert result.success

    def test_short_snowflake_rejected(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [{"name": "agenda", "value": "test"}],
                },
                "member": {"user": {"id": self._INVALID_SHORT}},
                "channel_id": self._VALID_SNOWFLAKE,
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success

    def test_alpha_snowflake_rejected(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [{"name": "agenda", "value": "test"}],
                },
                "member": {"user": {"id": self._INVALID_ALPHA}},
                "channel_id": self._VALID_SNOWFLAKE,
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success

    def test_too_long_snowflake_rejected(self):
        result = parse_meeting_request(
            payload={
                "data": {
                    "name": "meeting",
                    "options": [{"name": "agenda", "value": "test"}],
                },
                "member": {"user": {"id": self._INVALID_LONG}},
                "channel_id": self._VALID_SNOWFLAKE,
            },
            source=TriggerSource.DISCORD_SLASH,
        )
        assert not result.success

    def test_mention_empty_guild_ok(self):
        """Empty guild_id is valid (DM channels)."""
        result = parse_meeting_request(
            payload={
                "content": "test meeting",
                "author_id": self._VALID_SNOWFLAKE_17,
                "channel_id": self._VALID_SNOWFLAKE,
                "guild_id": "",
            },
            source=TriggerSource.DISCORD_MENTION,
        )
        assert result.success
        assert result.request.guild_id == ""


# ═════════════════════════════════════════════════════════════════════════
# TriggerSource enum
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerSource:
    """TriggerSource enum behaviour."""

    def test_all_sources_exist(self):
        sources = {s.value for s in TriggerSource}
        assert "discord_slash" in sources
        assert "discord_mention" in sources
        assert "webhook" in sources
        assert "direct_api" in sources

    def test_source_value_equal_to_name(self):
        for source in TriggerSource:
            assert source.value == source.name.lower()

    def test_string_comparison(self):
        assert TriggerSource.DISCORD_SLASH == "discord_slash"
        assert TriggerSource.WEBHOOK == "webhook"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError):
            parse_meeting_request(
                payload={"agenda": "test"},
                source="invalid",  # type: ignore[arg-type]
            )


# ═════════════════════════════════════════════════════════════════════════
# Bridge to downstream pipeline
# ═════════════════════════════════════════════════════════════════════════


class TestBridgeToPipeline:
    """meeting_request_to_parsed_trigger_input() bridge."""

    def test_bridge_basic(self):
        req = MeetingRequest(
            source=TriggerSource.DISCORD_SLASH,
            agenda="회의 주제",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            meeting_type="creative_production",
            priority="p1",
            teams=("art-director",),
            suggested_roles=("concept-artist",),
            participants=("art-director", "concept-artist"),
        )
        parsed = meeting_request_to_parsed_trigger_input(req)
        assert parsed.topic == "회의 주제"
        assert parsed.meeting_type == "creative_production"
        assert parsed.priority == "p1"
        assert parsed.teams == ("art-director",)
        assert parsed.suggested_roles == ("concept-artist",)
        assert parsed.participants == ("art-director", "concept-artist")
        assert parsed.is_meeting is True
        assert parsed.meeting_id == ""

    def test_bridge_default_priority(self):
        req = MeetingRequest(
            source=TriggerSource.DIRECT_API,
            agenda="test",
            user_id="u1",
            channel_id="c1",
        )
        parsed = meeting_request_to_parsed_trigger_input(req)
        assert parsed.priority == "p2"


# ═════════════════════════════════════════════════════════════════════════
# Internal helper tests
# ═════════════════════════════════════════════════════════════════════════


class TestHelperSafeGet:
    """_safe_get() utility."""

    def test_valid_dict_key(self):
        assert _safe_get({"a": "b"}, "a") == "b"

    def test_missing_key_returns_default(self):
        assert _safe_get({}, "a", default="fallback") == "fallback"

    def test_not_a_dict_returns_default(self):
        assert _safe_get("not a dict", "key", default="fallback") == "fallback"
        assert _safe_get(None, "key", default="fallback") == "fallback"
        assert _safe_get(42, "key", default="fallback") == "fallback"

    def test_none_key(self):
        assert _safe_get({"a": 1}, "b", default="default") == "default"


class TestHelperFirstNonEmpty:
    """_first_non_empty() utility."""

    def test_first_key_found(self):
        assert _first_non_empty({"a": "value"}, "a", "b") == "value"

    def test_second_key_fallback(self):
        assert (
            _first_non_empty({"a": "  ", "b": "real"}, "a", "b", "c")
            == "real"
        )

    def test_none_found(self):
        assert _first_non_empty({}, "a", "b") == ""

    def test_whitespace_value_skipped(self):
        assert _first_non_empty({"a": "   "}, "a", "b") == ""


class TestHelperExtractStringList:
    """_extract_string_list() utility."""

    def test_list_of_strings(self):
        result = _extract_string_list(
            {"items": ["a", "b", "c"]}, "items"
        )
        assert result == ["a", "b", "c"]

    def test_comma_separated_string(self):
        result = _extract_string_list(
            {"items": "a, b, c"}, "items"
        )
        assert result == ["a", "b", "c"]

    def test_missing_key(self):
        assert _extract_string_list({}, "missing") == []

    def test_empty_list(self):
        assert _extract_string_list({"items": []}, "items") == []

    def test_filters_empty_strings(self):
        result = _extract_string_list(
            {"items": ["a", "", "b", "  "]}, "items"
        )
        assert result == ["a", "b"]

    def test_mixed_types_in_list(self):
        result = _extract_string_list(
            {"items": ["a", 42, 3.14, "b"]}, "items"
        )
        assert result == ["a", "42", "3.14", "b"]


class TestHelperCoerceTuple:
    """_coerce_tuple() utility."""

    def test_tuple_passthrough(self):
        assert _coerce_tuple(("a", "b")) == ("a", "b")

    def test_list_to_tuple(self):
        assert _coerce_tuple(["a", "b"]) == ("a", "b")

    def test_string_to_tuple(self):
        assert _coerce_tuple("a, b, c") == ("a", "b", "c")

    def test_empty_string(self):
        assert _coerce_tuple("") == ()

    def test_whitespace_string(self):
        assert _coerce_tuple("   ") == ()

    def test_non_iterable(self):
        assert _coerce_tuple(42) == ()


class TestHelperCoerceFloat:
    """_coerce_float() utility."""

    def test_valid_float(self):
        assert _coerce_float(0.5) == 0.5

    def test_int(self):
        assert _coerce_float(1) == 1.0

    def test_string_float(self):
        assert _coerce_float("0.75") == 0.75

    def test_out_of_range_high(self):
        assert _coerce_float(1.5) == 1.0

    def test_out_of_range_low(self):
        assert _coerce_float(-0.5) == 0.0

    def test_invalid_defaults_to_1(self):
        assert _coerce_float("not_a_number") == 1.0
        assert _coerce_float(None) == 1.0


class TestHelperApplyOverrides:
    """_apply_overrides() utility."""

    def test_no_overrides(self):
        raw = _RawParsed(agenda="original")
        result = _apply_overrides(raw, {})
        assert result.agenda == "original"

    def test_override_agenda(self):
        raw = _RawParsed(agenda="original")
        result = _apply_overrides(raw, {"agenda": "overridden"})
        assert result.agenda == "overridden"

    def test_override_multiple_fields(self):
        raw = _RawParsed(agenda="a", user_id="u", channel_id="c")
        result = _apply_overrides(
            raw, {"agenda": "new_a", "priority": "p1", "confidence": 0.9}
        )
        assert result.agenda == "new_a"
        assert result.priority == "p1"
        assert result.confidence == 0.9


class TestHelperNormaliseAndValidate:
    """_normalise_and_validate() edge cases."""

    def test_thread_id_snowflake_validation_for_discord(self):
        raw = _RawParsed(
            agenda="test",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            thread_id="not_snowflake",
        )
        result = _normalise_and_validate(raw, TriggerSource.DISCORD_SLASH)
        assert not result.success
        assert "thread_id" in result.error

    def test_guild_id_snowflake_validation_for_discord(self):
        raw = _RawParsed(
            agenda="test",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            guild_id="bad_guild",
        )
        result = _normalise_and_validate(raw, TriggerSource.DISCORD_SLASH)
        assert not result.success
        assert "guild_id" in result.error

    def test_message_id_snowflake_validation_for_discord(self):
        raw = _RawParsed(
            agenda="test",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            message_id="bad_msg",
        )
        result = _normalise_and_validate(raw, TriggerSource.DISCORD_MENTION)
        assert not result.success
        assert "message_id" in result.error

    def test_empty_optional_snowflakes_ok(self):
        """Empty thread_id/guild_id/message_id are fine (no validation)."""
        raw = _RawParsed(
            agenda="test",
            user_id="123456789012345678",
            channel_id="987654321098765432",
            thread_id="",
            guild_id="",
            message_id="",
        )
        result = _normalise_and_validate(raw, TriggerSource.DISCORD_SLASH)
        assert result.success

    def test_source_specific_error_hint(self):
        """Source-specific error hints are included."""
        raw = _RawParsed(agenda="test", user_id="", channel_id="")
        result = _normalise_and_validate(raw, TriggerSource.DISCORD_SLASH)
        assert not result.success
        assert "slash command" in result.error.lower()


# ═════════════════════════════════════════════════════════════════════════
# Source-specific parser unit tests
# ═════════════════════════════════════════════════════════════════════════


class TestSourceParsersDirectly:
    """Test individual source parsers directly."""

    def test_parse_discord_slash_missing_member(self):
        parsed = _parse_discord_slash(
            {
                "data": {
                    "name": "meeting",
                    "options": [{"name": "agenda", "value": "test"}],
                },
                "channel_id": "123456789012345678",
            }
        )
        # Should not crash — user_id will be empty, caught by validator
        assert parsed.agenda == "test"
        assert parsed.user_id == ""

    def test_parse_discord_slash_corrupt_options(self):
        """Non-list options don't crash."""
        parsed = _parse_discord_slash(
            {
                "data": {
                    "name": "meeting",
                    "options": "not a list",
                },
                "member": {"user": {"id": "123456789012345678"}},
                "channel_id": "987654321098765432",
            }
        )
        # agenda won't be found since options isn't iterable
        assert parsed.agenda == ""

    def test_parse_webhook_empty_payload(self):
        parsed = _parse_webhook({})
        assert parsed.agenda == ""
        assert parsed.user_id == ""

    def test_parse_direct_api_empty_payload(self):
        parsed = _parse_direct_api({})
        assert parsed.agenda == ""
        assert parsed.user_id == ""

    def test_parse_discord_mention_minimal(self):
        parsed = _parse_discord_mention(
            {
                "content": "회의하자",
                "author_id": "123456789012345678",
                "channel_id": "987654321098765432",
            }
        )
        assert parsed.agenda == "회의하자"
        assert parsed.user_id == "123456789012345678"
        assert parsed.confidence == 0.90
